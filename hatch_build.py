"""Embed source provenance without modifying the source tree during builds."""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)

        def git(*args):
            try:
                result = subprocess.run(
                    ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
                )
            except FileNotFoundError:
                return "unknown"
            return result.stdout.strip() if result.returncode == 0 else "unknown"

        digest = hashlib.sha256()
        for path in sorted((root / "src/apitest").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
        self._provenance_dir = tempfile.TemporaryDirectory(prefix="repo2test-build-")
        provenance = Path(self._provenance_dir.name) / "build_info.json"
        provenance.write_text(
            json.dumps(
                {
                    "source_commit": git("rev-parse", "HEAD"),
                    "source_dirty": bool(git("status", "--porcelain")),
                    "source_sha256": digest.hexdigest(),
                },
                sort_keys=True,
            )
            + "\n"
        )
        build_data["force_include"][str(provenance)] = "apitest/build_info.json"

    def finalize(self, version, build_data, artifact_path):
        self._provenance_dir.cleanup()
