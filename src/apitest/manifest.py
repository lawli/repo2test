"""Cleanup manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, Any


class ManifestWriter:
    def __init__(self, path: Path | str, *, run_id: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] = self._path.open("a", buffering=1)
        self._run_id = run_id

    def append(self, *, case_id: str, service: str, table: str, id_value: Any) -> None:
        record = {
            "run_id": self._run_id,
            "case_id": case_id,
            "service": service,
            "table": table,
            "id_value": id_value,
        }
        self._fh.write(json.dumps(record) + "\n")

    def close(self) -> None:
        self._fh.close()


def read_manifest(path: Path | str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in Path(path).read_text().splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        out.append(json.loads(stripped))
    return out
