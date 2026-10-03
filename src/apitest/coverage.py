"""Per-endpoint coverage records and a mechanical route registration cross-check."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    line: int = Field(ge=1)
    kind: Literal["route", "requirement", "declarative", "test", "implementation"] = "route"


class CoverageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    variant: str = "default"
    source_commit: str
    status: Literal["uncovered", "authored", "blocked", "needs-review", "gap"] = "uncovered"
    expectation: Literal["confirmed", "pending"] = "confirmed"
    reason: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _explain_open_rows(self) -> CoverageRow:
        if self.status in {"gap", "blocked", "needs-review"} and not (self.reason or "").strip():
            raise ValueError(f"coverage row {self.key!r} with status {self.status} needs a reason")
        return self


class Endpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    method: str
    path: str
    conditions: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    rows: list[CoverageRow] = Field(default_factory=list)


def load_inventory(root: Path) -> list[tuple[Path, Endpoint]]:
    inventory = []
    for p in sorted(root.rglob("*.yaml")):
        try:
            inventory.append((p, Endpoint.model_validate(yaml.safe_load(p.read_text()))))
        except (ValueError, yaml.YAMLError) as exc:
            raise ValueError(f"{p}: {exc}") from exc
    return inventory


def _mask_literals(text: str, *, kotlin: bool = False) -> str:
    """Mask JVM comments, strings and character literals without moving lines."""
    masked = list(text)
    index = 0
    while index < len(text):
        start = index
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
        elif text.startswith("/*", index):
            depth = 1
            index += 2
            while index < len(text) and depth:
                if kotlin and text.startswith("/*", index):
                    depth += 1
                    index += 2
                elif text.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
        elif text.startswith('"""', index):
            index += 3
            while index < len(text) and not text.startswith('"""', index):
                index += 2 if not kotlin and text[index] == "\\" else 1
            index = min(index + 3, len(text))
        elif text[index] in ('"', "'"):
            quote = text[index]
            index += 1
            while index < len(text) and text[index] not in (quote, "\n", "\r"):
                index += 2 if text[index] == "\\" else 1
            if index < len(text) and text[index] == quote:
                index += 1
        else:
            index += 1
            continue
        for offset in range(start, min(index, len(text))):
            if text[offset] not in "\r\n":
                masked[offset] = " "
    return "".join(masked)


def _test_source(parts: tuple[str, ...]) -> bool:
    # Match source-set roots, never package names such as com.example.test.
    for directory, source_set in zip(parts, parts[1:], strict=False):
        if directory == "src":
            if source_set in {"main", "java", "kotlin"}:
                return False
            if source_set in {"test", "testFixtures", "androidTest"}:
                return True
    return False


def spring_mappings(source: Path) -> list[dict[str, Any]]:
    """Return syntax evidence, not inferred runtime routes or business contracts."""
    pattern = re.compile(
        r"@(?:[\w.]+\.)?(?:Request|Get|Post|Put|Patch|Delete|Head|Options)Mapping\b"
        r"|@(?:[\w.]+\.)?(?:Endpoint|WebEndpoint|ControllerEndpoint|RestControllerEndpoint|"
        r"ServletEndpoint|ReadOperation|WriteOperation|DeleteOperation)\b"
        r"|\bRouterFunction(?:\s*<[^;{}]*?>)?\s+\w+\s*\("
        r"|\bfun\s+\w+\s*\([^)]*\)\s*:\s*RouterFunction\b"
    )
    found = []
    for path in sorted([*source.rglob("*.java"), *source.rglob("*.kt")]):
        parts = path.relative_to(source).parts
        if ".git" in parts or _test_source(parts):
            continue
        text = _mask_literals(path.read_text(), kotlin=path.suffix == ".kt")
        for match in pattern.finditer(text):
            found.append(
                {
                    "file": path.relative_to(source).as_posix(),
                    "line": text.count("\n", 0, match.start()) + 1,
                    "syntax": match.group().strip(),
                }
            )
    return found


_SKIPPED_DIRS = {".git", ".venv", "venv", "site-packages", "node_modules"}
_PY_TEST_MODULE = re.compile(r"(?:test_.*|.*_test|conftest)\.py")
_FASTAPI_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+fastapi\b", re.MULTILINE)
_FASTAPI_ROUTE = re.compile(
    r"^[ \t]*@[\w.]+\."
    r"(?:get|post|put|patch|delete|head|options|trace|api_route|websocket)[ \t]*\(",
    re.MULTILINE,
)


def _mapping(source: Path, path: Path, text: str, match: re.Match[str]) -> dict[str, Any]:
    matched = match.group()
    start = match.start() + len(matched) - len(matched.lstrip())
    return {
        "file": path.relative_to(source).as_posix(),
        "line": text.count("\n", 0, start) + 1,
        "syntax": matched.strip().splitlines()[0],
    }


def fastapi_mappings(source: Path) -> list[dict[str, Any]]:
    """Route decorators at their first line; empty unless the source imports fastapi."""
    texts = [
        (path, path.read_text(errors="replace"))
        for path in sorted(source.rglob("*.py"))
        if not _SKIPPED_DIRS.intersection(path.relative_to(source).parts)
        and not _PY_TEST_MODULE.fullmatch(path.name)
    ]
    if not any(_FASTAPI_IMPORT.search(text) for _, text in texts):
        return []
    return [
        _mapping(source, path, text, match)
        for path, text in texts
        for match in _FASTAPI_ROUTE.finditer(text)
    ]


def pattern_mappings(
    source: Path, patterns: Sequence[str], include: Sequence[str]
) -> tuple[list[dict[str, Any]], int]:
    """Matches of caller-supplied route syntax in the included files, and the file count."""
    try:
        compiled = [re.compile(pattern, re.MULTILINE) for pattern in patterns]
    except re.error as exc:
        raise ValueError(f"--pattern is not a valid regular expression: {exc}") from exc
    for glob in include:
        if glob.startswith("/") or ".." in Path(glob).parts:
            raise ValueError(f"--include must be a glob inside the source: {glob}")
    files = sorted(
        {
            path
            for glob in include
            for path in (source.glob(glob) if "/" in glob else source.rglob(glob))
            if path.is_file() and not _SKIPPED_DIRS.intersection(path.relative_to(source).parts)
        }
    )
    found = []
    for path in files:
        text = path.read_text(errors="replace")
        found += [
            _mapping(source, path, text, match)
            for pattern in compiled
            for match in pattern.finditer(text)
            if match.group().strip()
        ]
    return sorted(found, key=lambda m: (m["file"], m["line"])), len(files)


def reconcile(
    source: Path, inventory: Path, *, patterns: Sequence[str] = (), include: Sequence[str] = ()
) -> dict[str, Any]:
    recorded = {
        (e.file, e.line)
        for _, endpoint in load_inventory(inventory)
        for e in endpoint.evidence + [e for row in endpoint.rows for e in row.evidence]
        if e.kind == "route"
    }
    scans = {"spring": spring_mappings(source), "fastapi": fastapi_mappings(source)}
    files = 0
    if patterns:
        scans["pattern"], files = pattern_mappings(source, patterns, include)
    mappings: dict[tuple[str, int], dict[str, Any]] = {}
    for scanner, found in scans.items():
        for m in found:
            mappings.setdefault((m["file"], m["line"]), {**m, "scanner": scanner})
    result: dict[str, Any] = {
        "scope": "syntactic route registration evidence; not a runtime route inventory",
        # When no scanner matched there is nothing to compare, so an empty gap list
        # says nothing about whether the inventory is complete.
        "cross_check": "+".join(name for name, found in scans.items() if found) or "unavailable",
        "mappings": list(mappings.values()),
        "discovery_gaps": [m for at, m in mappings.items() if at not in recorded],
    }
    if patterns:
        result["pattern"] = {
            "patterns": list(patterns),
            "include": list(include),
            "files": files,
            "mappings": len(scans["pattern"]),
            # Cited registrations no scan found: the patterns or globs miss that form,
            # so an empty gap list would not mean the inventory is complete.
            "unmatched_evidence": [
                {"file": file, "line": line} for file, line in sorted(recorded - mappings.keys())
            ],
        }
    return result


def coverage_view(
    inventory: Path, cases: list[dict[str, Any]], results: dict[str, Any] | None = None
) -> dict[str, Any]:
    authored = {
        (c["key"], c.get("variant", "default")) for case in cases for c in case.get("covers", [])
    }
    passed = {
        (c["key"], c.get("variant", "default"), case.get("source_commit"))
        for case in (results or {}).get("cases", [])
        if case.get("outcome") == "PASS" and case.get("expectation") == "confirmed"
        for c in case.get("covers", [])
    }
    rows = []
    for _, endpoint in load_inventory(inventory):
        for row in endpoint.rows:
            identity = (row.key, row.variant)
            rows.append(
                {
                    **row.model_dump(),
                    "service": endpoint.service,
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "authored": identity in authored,
                    "verified": (
                        row.expectation == "confirmed"
                        and identity in authored
                        and row.status not in {"blocked", "needs-review", "gap"}
                        and (*identity, row.source_commit) in passed
                    ),
                }
            )
    return {
        "run": (results or {}).get("run"),
        "rows": rows,
        "pending": sum(not r["authored"] for r in rows),
        "authored": sum(r["authored"] for r in rows),
        "pending_expectations": sum(r["expectation"] == "pending" for r in rows),
        "blocked": sum(r["status"] == "blocked" for r in rows),
        "needs_review": sum(r["status"] == "needs-review" for r in rows),
        "gaps": sum(r["status"] == "gap" for r in rows),
        "verified": sum(r["verified"] for r in rows),
        # Confirmed rows whose only contract evidence is a test of the business repository.
        "test_backed": sum(
            r["expectation"] == "confirmed"
            and {e["kind"] for e in r["evidence"]} & {"requirement", "declarative", "test"}
            == {"test"}
            for r in rows
        ),
    }
