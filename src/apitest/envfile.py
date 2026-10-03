"""`.env` loading shared by the CLI and the pytest plugin."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(directory: Path) -> None:
    """Load KEY=VALUE pairs from directory/.env. Variables already set always win."""
    env_file = directory / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
