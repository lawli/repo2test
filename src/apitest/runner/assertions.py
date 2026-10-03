"""Response assertion DSL."""

from __future__ import annotations

import json
import operator
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any


class AssertionMismatch(AssertionError):
    def __init__(self, message: str, *, at: str | None = None) -> None:
        super().__init__(message)
        self.at = at


_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


# JSON type names -> Python types; Python names are matched by __name__.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "object": (dict,),
    "array": (list,),
    "boolean": (bool,),
    "null": (type(None),),
}


def _resolve_dotted(body: Any, dotted: str) -> Any:
    # `$` and `$.a.b` follow the JSONPath spelling that `extract` uses.
    if dotted == "$":
        return body
    cur: Any = body
    for part in dotted.removeprefix("$.").split("."):
        if isinstance(cur, dict):
            cur = cur[part]
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            raise KeyError(dotted)
    return cur


def _match_pattern(actual: Any, expected: Any) -> tuple[bool, str]:
    try:
        return _match_dsl(actual, expected)
    except (ValueError, TypeError):
        return False, f"malformed matcher DSL {expected!r} (or not applicable to {actual!r})"


def _match_dsl(actual: Any, expected: Any) -> tuple[bool, str]:
    if isinstance(expected, str) and expected.startswith("@@"):
        # Escape: "@@alice" expects the literal "@alice".
        literal = expected[1:]
        return actual == literal, f"expected {literal!r}, got {actual!r}"
    if isinstance(expected, str) and expected.startswith("@"):
        if expected == "@any":
            return True, ""
        if expected == "@number > 0":
            return (
                isinstance(actual, (int, float)) and actual > 0,
                f"expected number>0, got {actual!r}",
            )
        if expected.startswith("@number"):
            tail = expected[len("@number") :].strip()
            for sym in (">=", "<=", "==", "!=", ">", "<"):
                if tail.startswith(sym):
                    rhs = float(tail[len(sym) :].strip())
                    ok = isinstance(actual, (int, float)) and _OPS[sym](actual, rhs)
                    return ok, f"expected {expected!r}, got {actual!r}"
        if expected == "@uuid":
            try:
                uuid.UUID(str(actual))
                return True, ""
            except (ValueError, TypeError):
                return False, f"expected uuid, got {actual!r}"
        if expected == "@iso8601":
            try:
                datetime.fromisoformat(str(actual).replace("Z", "+00:00"))
                return True, ""
            except (ValueError, TypeError):
                return False, f"expected iso8601, got {actual!r}"
        if expected.startswith("@regex "):
            m = re.match(r"@regex\s+/(.*)/$", expected)
            if not m:
                return False, f"malformed @regex DSL: {expected!r}"
            return (
                bool(re.search(m.group(1), str(actual))),
                f"expected match {expected!r}, got {actual!r}",
            )
        if expected.startswith("@type"):
            tail = expected[len("@type") :].strip()
            if tail in _JSON_TYPES:
                ok = isinstance(actual, _JSON_TYPES[tail])
                if tail in ("number", "integer") and isinstance(actual, bool):
                    ok = False
            else:
                ok = type(actual).__name__ == tail
            return ok, f"expected type {tail}, got {type(actual).__name__}"
        if expected == "@null":
            return actual is None, f"expected null, got {actual!r}"
        if expected.startswith("@len"):
            tail = expected[len("@len") :].strip()
            try:
                size = len(actual)
            except TypeError:
                return False, f"expected a sized value for {expected!r}, got {actual!r}"
            for sym in (">=", "<=", "==", "!=", ">", "<"):
                if tail.startswith(sym):
                    rhs = float(tail[len(sym) :].strip())
                    return _OPS[sym](size, rhs), f"expected {expected!r}, got len {size}"
            return size == int(tail), f"expected len {tail}, got len {size}"
        if expected.startswith("@contains "):
            needle_text = expected[len("@contains ") :]
            if isinstance(actual, str):
                return needle_text in actual, f"expected {expected!r}, got {actual!r}"
            if isinstance(actual, (list, dict)):
                needle = _parse_literal(needle_text)
                return (
                    needle in actual or needle_text in actual,
                    f"expected {expected!r}, got {actual!r}",
                )
            return False, f"expected a container for {expected!r}, got {actual!r}"
        if expected.startswith("@in "):
            options = json.loads(expected[len("@in ") :])
            return actual in options, f"expected one of {options!r}, got {actual!r}"
    return (actual == expected, f"expected {expected!r}, got {actual!r}")


def _parse_literal(text: str) -> Any:
    """`@contains 2` should match the number 2 in a list; fall back to the text."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def assert_response(
    *,
    status: int,
    body: Any,
    expected: dict[str, Any],
    headers: Mapping[str, str] | None = None,
    text: str | None = None,
) -> None:
    want = expected.get("status")
    if isinstance(want, list):
        if status not in want:
            raise AssertionMismatch(f"status: expected one of {want}, got {status}", at="status")
    elif want is not None and want != status:
        raise AssertionMismatch(f"status: expected {want}, got {status}", at="status")
    for path, exp_val in (expected.get("json") or {}).items():
        try:
            actual = _resolve_dotted(body, path)
        except (KeyError, IndexError, TypeError, ValueError):
            if exp_val == "@absent":
                continue
            raise AssertionMismatch(
                f"json path {path!r} not present in body", at=f"json.{path}"
            ) from None
        if exp_val == "@absent":
            raise AssertionMismatch(
                f"json.{path}: expected absent, got {actual!r}", at=f"json.{path}"
            )
        ok, why = _match_pattern(actual, exp_val)
        if not ok:
            raise AssertionMismatch(f"json.{path}: {why}", at=f"json.{path}")
    if expected.get("headers"):
        lowered = {k.lower(): v for k, v in (headers or {}).items()}
        for name, exp_val in expected["headers"].items():
            if name.lower() not in lowered:
                raise AssertionMismatch(
                    f"header {name!r} not present in response", at=f"headers.{name.lower()}"
                )
            ok, why = _match_pattern(lowered[name.lower()], exp_val)
            if not ok:
                raise AssertionMismatch(f"header {name}: {why}", at=f"headers.{name.lower()}")
    if expected.get("text") is not None:
        ok, why = _match_pattern(text if text is not None else "", expected["text"])
        if not ok:
            raise AssertionMismatch(f"text: {why}", at="text")
