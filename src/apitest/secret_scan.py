"""Static secret-pattern scan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    ("AWS_SECRET_HINT", re.compile(r"aws_secret_access_key\s*[:=]")),
    ("DSN_WITH_PASSWORD", re.compile(r"://[^:\s]+:[^@/\s]+@")),
    ("JWT", re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")),
    (
        "HIGH_ENTROPY",
        re.compile(
            r"(?i)\b(password|secret|token|api[_-]?key)\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9!@#$%^&*+/=]{16,}"
        ),
    ),
]
_ALLOW_MARKER = re.compile(r"#\s*apitest:\s*allow-secret-pattern")


@dataclass
class SecretFinding:
    kind: str
    path: Path
    line: int
    snippet: str


def scan_text(text: str, *, path: Path) -> list[SecretFinding]:
    out: list[SecretFinding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if _ALLOW_MARKER.search(line):
            continue
        for kind, rx in _PATTERNS:
            if rx.search(line):
                out.append(SecretFinding(kind=kind, path=path, line=i, snippet=line.strip()[:120]))
                break
    return out


def scan_file(path: Path) -> list[SecretFinding]:
    return scan_text(Path(path).read_text(), path=path)
