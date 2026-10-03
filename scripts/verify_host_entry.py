"""Run a bounded, real host skill-discovery probe in disposable sibling repositories."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", choices=["codex", "claude"])
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix=f"repo2test-{args.host}-probe-"))
    business, workspace = root / "business", root / "business-e2e"
    business.mkdir()
    workspace.mkdir()
    token = secrets.token_hex(12)
    (business / "probe-source.txt").write_text(token)
    folder = ".agents" if args.host == "codex" else ".claude"
    skill = business / folder / "skills" / "repo2test-feasibility"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: repo2test-feasibility\n"
        "description: Verify the disposable repo2test sibling-workspace access probe.\n---\n"
        "Read probe-source.txt in the current business directory. Write its exact contents "
        f"to {workspace / 'probe-result.txt'}. Read only this skill and probe-source.txt; "
        "write only that result file. Do not run tests, access services, or inspect credentials.\n"
    )
    executable = shutil.which(args.host)
    if not executable:
        raise SystemExit(f"{args.host} not installed")
    if args.host == "codex":
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--add-dir",
            str(workspace),
            "$repo2test-feasibility Run the disposable access probe.",
        ]
    else:
        command = [
            executable,
            "--print",
            "--no-session-persistence",
            "--max-turns",
            "8",
            "--permission-mode",
            "acceptEdits",
            "--add-dir",
            str(workspace),
            "--allowedTools",
            "Read,Write,Edit,Bash",
            "--",
            "/repo2test-feasibility Run the disposable access probe.",
        ]
    try:
        result = subprocess.run(
            command,
            cwd=business,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
        log = result.stdout + result.stderr
        code = result.returncode
    except subprocess.TimeoutExpired as exc:
        log = str(exc)
        code = 124
    (root / "host.log").write_text(log)
    output = workspace / "probe-result.txt"
    passed = output.is_file() and output.read_text() == token
    print(
        json.dumps(
            {"host": args.host, "passed": passed, "returncode": code, "artifacts": str(root)}
        )
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
