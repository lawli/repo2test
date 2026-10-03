"""Install a versioned user entry from a standalone test-workspace clone."""

import argparse
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--host", choices=["codex", "claude"], required=True)
parser.add_argument(
    "--directory", type=Path, help="Explicit skills directory, e.g. for an isolated test"
)
parser.add_argument("--update", action="store_true")
args = parser.parse_args()
parent = (
    args.directory or Path.home() / (".agents" if args.host == "codex" else ".claude") / "skills"
)
destination = parent / "repo2test"
source = Path(__file__).resolve().parent / "entry" / "repo2test"
if destination.is_symlink():
    raise SystemExit("Existing entry is a symlink; select its owning installation explicitly")
if destination.exists() and not args.update:
    raise SystemExit(f"Entry exists at {destination}; use --update for a deliberate update")
staging = destination.with_name(".repo2test-update")
backup = destination.with_name(".repo2test-previous")
# An interrupted earlier update may have left either directory behind.
shutil.rmtree(staging, ignore_errors=True)
if destination.exists():
    # Replace the whole entry so files removed from a newer version do not linger.
    shutil.rmtree(backup, ignore_errors=True)
    try:
        shutil.copytree(source, staging)
        destination.rename(backup)
        try:
            staging.rename(destination)
        except OSError as exc:
            try:
                backup.rename(destination)
            except OSError:
                raise SystemExit(
                    f"Update failed ({exc}); the previous entry is at {backup}. "
                    f"Rename it to {destination} to restore it."
                ) from exc
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
else:
    shutil.copytree(source, destination)
    shutil.rmtree(backup, ignore_errors=True)
print(f"Installed {destination}. Existing workspace rules and runners remain pinned.")
print(
    'Codex: launch with --add-dir <workspace> --add-dir "$(uv cache dir)"; when uv must '
    "download packages, also pass -c sandbox_workspace_write.network_access=true."
    if args.host == "codex"
    else "Claude Code: add the test workspace with --add-dir <workspace>."
)
