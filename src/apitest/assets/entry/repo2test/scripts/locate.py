"""Locate a test workspace without requiring the runner to be installed."""

import argparse
import json
import sys
import tempfile
import tomllib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--start", default=".")
args = parser.parse_args()
start = Path(args.start).resolve()
ancestors = [start, *start.parents]
root = next((p for p in ancestors if (p / "repo2test.toml").is_file()), None)
inside_workspace = root is not None
business = next((p for p in ancestors if (p / ".git").exists()), start)
if root is None:
    candidate = business.parent / f"{business.name}-e2e"
    if (candidate / "repo2test.toml").is_file():
        root = candidate
if root is None:
    print(
        json.dumps(
            {
                "status": "workspace-missing",
                "business": str(business),
                "suggested": str(business.parent / f"{business.name}-e2e"),
            }
        )
    )
    raise SystemExit(2)
config = tomllib.loads((root / "repo2test.toml").read_text())
target = config.get("target", {}).get("path")
skill = (root / config.get("paths", {}).get("skill", "")).resolve()
if not skill.is_relative_to(root) or not skill.is_file():
    raise SystemExit("Invalid or missing workspace-pinned skill")
if target and not inside_workspace and (root / target).resolve() != business:
    raise SystemExit("Sibling workspace targets a different business repository")
writable = True
try:
    with tempfile.TemporaryFile(dir=root):
        pass
except OSError:
    writable = False
print(
    json.dumps(
        {
            "status": "ready" if writable and target else "configuration-required",
            "business": str((root / target).resolve()) if target else None,
            "workspace": str(root),
            "skill": str(skill),
            "writable": writable,
            "sync_required": True,
            "python": sys.version.split()[0],
        }
    )
)
