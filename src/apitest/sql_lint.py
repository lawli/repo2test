"""SQL-fixture linter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SqlLintError:
    kind: str
    path: Path
    line: int
    snippet: str


_INSERT_VALUES_RE = re.compile(r"\bINSERT\s+INTO\b.*\bVALUES\s*\((.*)\)", re.IGNORECASE | re.DOTALL)
_LITERAL_INT_RE = re.compile(r"(?<![.\w$])\d{2,}(?![.\w$])")
_ENV_VAR_RE = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}")
_CTX_RE = re.compile(r"\$\{ctx\.[a-zA-Z0-9_.]+\}")
_BLANKET_DELETE_RE = re.compile(r"\bDELETE\s+FROM\s+\w+\s*;", re.IGNORECASE)
_ALLOW_LITERAL = re.compile(r"--\s*allow-literal-id")


def lint_sql(text: str, *, path: Path) -> list[SqlLintError]:
    issues: list[SqlLintError] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if _ENV_VAR_RE.search(line):
            issues.append(SqlLintError("env-var-not-allowed", path, i, line.strip()[:120]))

        m = _INSERT_VALUES_RE.search(line)
        if m and not _ALLOW_LITERAL.search(line):
            values = m.group(1)
            stripped = _CTX_RE.sub("X", values)
            if _LITERAL_INT_RE.search(stripped):
                issues.append(SqlLintError("literal-id", path, i, line.strip()[:120]))

        if _BLANKET_DELETE_RE.search(line):
            issues.append(SqlLintError("blanket-delete", path, i, line.strip()[:120]))
    return issues
