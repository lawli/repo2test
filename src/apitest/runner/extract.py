"""JSONPath extraction for step `extract:` blocks."""

from __future__ import annotations

from typing import Any

from jsonpath_ng import parse as _parse


def extract_jsonpath(body: Any, path: str) -> Any:
    expr = _parse(path)
    matches = [m.value for m in expr.find(body)]
    if not matches:
        raise KeyError(f"jsonpath {path} not found")
    return matches[0] if len(matches) == 1 else matches
