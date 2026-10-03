"""Taint-based redaction."""

from __future__ import annotations

from typing import Any

_TAINT_REGISTRY: set[str] = set()  # tainted literal values seen this run


class TaintedStr(str):
    """A str subclass that remembers it came from an env var.

    Concatenation produces plain str; we track the literal value in a
    registry so any later occurrence of the same substring redacts.
    """

    def __new__(cls, value: str, source: str) -> TaintedStr:
        obj = super().__new__(cls, value)
        obj._source = source  # type: ignore[attr-defined]
        if value:
            _TAINT_REGISTRY.add(value)
        return obj

    @property
    def source(self) -> str:
        return str(self._source)  # type: ignore[attr-defined]


def is_tainted(value: object) -> bool:
    if isinstance(value, TaintedStr):
        return True
    if isinstance(value, str):
        return any(t in value for t in _TAINT_REGISTRY if t)
    return False


def redact(value: Any) -> Any:
    """Recursively replace any tainted substring with `***`."""
    if isinstance(value, str):
        out = value
        for tainted in sorted(_TAINT_REGISTRY, key=len, reverse=True):
            if tainted and tainted in out:
                out = out.replace(tainted, "***")
        return out
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        seq = [redact(v) for v in value]
        return type(value)(seq)
    return value


def reset_taint() -> None:
    """Clear the taint registry (test-only)."""
    _TAINT_REGISTRY.clear()


def register_secrets(value: Any) -> None:
    """Track credentials returned by login flows as well as configured secrets."""
    sensitive = {
        "authorization",
        "cookie",
        "setcookie",
        "password",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "clientsecret",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("_", "").replace("-", "")
            if normalized in sensitive and isinstance(item, str) and item:
                _TAINT_REGISTRY.add(item)
            else:
                register_secrets(item)
    elif isinstance(value, list):
        for item in value:
            register_secrets(item)
