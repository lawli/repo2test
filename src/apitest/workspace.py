"""Workspace identity and path boundaries shared by the CLI and pytest."""

from __future__ import annotations

import hashlib
import subprocess
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def find_workspace(start: Path | str = ".") -> Path | None:
    path = Path(start).resolve()
    if path.is_file():
        path = path.parent
    return next((p for p in (path, *path.parents) if (p / "repo2test.toml").is_file()), None)


def contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"workspace path escapes root: {relative}")
    return path


@dataclass
class Workspace:
    root: Path
    config: dict[str, Any]

    @classmethod
    def load(cls, start: Path | str = ".") -> Workspace:
        root = find_workspace(start)
        if root is None:
            raise ValueError("repo2test.toml not found; select or initialize a test workspace")
        config = tomllib.loads((root / "repo2test.toml").read_text())
        if config.get("format", 1) != 1:
            raise ValueError("Unsupported repo2test workspace format")
        runner_path = root / "runner.toml"
        if runner_path.is_file():
            if "runner" in config:
                raise ValueError("runner metadata is duplicated in runner.toml and repo2test.toml")
            config["runner"] = tomllib.loads(runner_path.read_text()).get("runner", {})
        return cls(root, config)

    def path(self, name: str, default: str | None = None) -> Path:
        return contained(self.root, str(self.config.get("paths", {}).get(name, default or name)))

    @property
    def source_commit(self) -> str:
        return str(self.config.get("reference", {}).get("commit", ""))

    def runner_problems(self) -> list[str]:
        """Compare executable instructions and entry assets with the pinned wheel."""
        from apitest.bundle import Bundle

        runner = self.config.get("runner", {})
        required = ("wheel", "sha256", "version", "source_commit", "source_sha256")
        if not isinstance(runner, dict) or any(
            not isinstance(runner.get(key), str) or not runner[key].strip() for key in required
        ):
            return ["runner metadata is required: " + ", ".join(required)]
        try:
            wheel = contained(self.root, runner["wheel"])
            if not wheel.is_file() or hashlib.sha256(wheel.read_bytes()).hexdigest() != runner.get(
                "sha256"
            ):
                return ["pinned runner wheel is missing or its SHA256 differs"]
            bundle = Bundle.read(wheel)
            errors = bundle.asset_errors(self.root)
            expected = {"version": bundle.version, **bundle.provenance}
            for key in ("version", "source_commit", "source_sha256"):
                if runner[key] != expected.get(key):
                    errors.append(f"runner.{key} differs from pinned wheel metadata")
            canonical = self.root / ".claude/skills/repo2test-workspace/SKILL.md"
            if self.path("skill") != canonical:
                errors.append("paths.skill must select the workspace skill bundled in the wheel")
            return errors
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            return [f"invalid pinned runner distribution: {exc}"]

    def identity(self) -> dict[str, Any]:
        def git(*args: str) -> str | None:
            proc = subprocess.run(
                ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False
            )
            return proc.stdout.strip() if proc.returncode == 0 else None

        digest = hashlib.sha256()
        paths = [
            self.root / p for p in ("repo2test.toml", "runner.toml", "pyproject.toml", "uv.lock")
        ]
        for name in ("tests", "profiles", "coverage"):
            paths.extend(self.path(name).rglob("*"))
        for path in sorted(set(paths)):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(self.root).as_posix().encode())
                digest.update(path.read_bytes())
        return {
            "workspace_commit": git("rev-parse", "HEAD"),
            "workspace_fingerprint": digest.hexdigest(),
            "workspace_ref": git("symbolic-ref", "--short", "HEAD"),
            "source_commit": self.source_commit,
            "reference_ref": self.config.get("reference", {}).get("ref"),
            "reference_estimated": self.config.get("reference", {}).get("estimated", False),
            "reference_evidence": self.config.get("reference", {}).get("evidence"),
            "runner_version": self.config.get("runner", {}).get("version"),
            "runner_sha256": self.config.get("runner", {}).get("sha256"),
        }
