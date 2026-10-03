"""Read pinned assets from the supplied wheel, never from the bootstrap runner."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass
class Bundle:
    version: str
    provenance: dict[str, Any]
    assets: dict[str, bytes]

    @classmethod
    def read(cls, wheel: Path) -> Bundle:
        with zipfile.ZipFile(wheel) as archive:
            metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError("Invalid runner wheel metadata")
            text = archive.read(metadata[0]).decode()
            version = re.search(r"^Version: (.+)$", text, re.M)
            if not re.search(r"^Name: apitest$", text, re.M) or version is None:
                raise ValueError("Expected an apitest runner wheel")
            prefix = "apitest/assets/"
            assets = {}
            for name in archive.namelist():
                if not name.startswith(prefix) or name.endswith("/"):
                    continue
                rel = name.removeprefix(prefix)
                if ".." in PurePosixPath(rel).parts or rel.startswith("/"):
                    raise ValueError("Invalid wheel asset path")
                assets[rel] = archive.read(name)
            required = {
                "entry/repo2test/SKILL.md",
                "entry/repo2test/scripts/locate.py",
                "skills/repo2test-workspace/SKILL.md",
                "install_entry.py",
            }
            if not required <= assets.keys():
                raise ValueError("Runner wheel lacks the matching skills and entry installer")
            try:
                provenance = json.loads(archive.read("apitest/build_info.json"))
            except KeyError as exc:
                raise ValueError("Runner wheel lacks source provenance (build_info.json)") from exc
            if not provenance.get("source_commit") or not provenance.get("source_sha256"):
                raise ValueError("Runner wheel lacks source provenance")
            return cls(version.group(1), provenance, assets)

    def installed_assets(self) -> dict[str, bytes]:
        """Canonical workspace paths and their wheel-owned contents."""
        installed = {}
        for rel, data in self.assets.items():
            if rel.startswith("skills/"):
                installed[f".claude/{rel}"] = data
            elif rel.startswith("entry/") or rel == "install_entry.py":
                installed[f"tools/{rel}"] = data
        return installed

    def install_assets(self, root: Path) -> None:
        for rel, data in self.installed_assets().items():
            destination = root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

    def protect_checkout(self, root: Path) -> None:
        """Keep wheel-owned bytes unchanged by Git's platform newline conversion."""
        path = root / ".gitattributes"
        existing = path.read_text() if path.exists() else ""
        start, end = "# repo2test pinned assets", "# end repo2test pinned assets"
        existing = re.sub(
            re.escape(start) + r".*?" + re.escape(end) + r"\n?", "", existing, flags=re.S
        )
        rules = [
            "/.claude/skills/repo2test-workspace/** -text",
            "/tools/entry/** -text",
            "/tools/install_entry.py -text",
            "/vendor/*.whl -text",
        ]
        path.write_text(existing.rstrip() + "\n" + "\n".join([start, *rules, end]) + "\n")

    def asset_errors(self, root: Path) -> list[str]:
        expected = self.installed_assets()
        errors = []
        for rel, content in expected.items():
            path = root / rel
            if path.resolve() != path or not path.is_file() or path.read_bytes() != content:
                errors.append(f"pinned asset differs from runner wheel: {rel}")
        for directory in (".claude/skills/repo2test-workspace", "tools/entry"):
            for path in (root / directory).rglob("*"):
                if "__pycache__" in path.parts:
                    continue
                if _local_clutter(path.name) and (
                    not path.is_symlink() or path.name.startswith(".#")
                ):
                    continue
                rel = path.relative_to(root).as_posix()
                if (path.is_file() or path.is_symlink()) and rel not in expected:
                    errors.append(f"unexpected pinned asset: {rel}")
        return errors


def _local_clutter(name: str) -> bool:
    """OS metadata and editor swap/backup files, never shipped in a wheel."""
    return (
        name in {".DS_Store", "Thumbs.db", "desktop.ini"}
        or name.endswith(("~", ".swp", ".swo", ".swx"))
        or name.startswith(".#")
        or (name.startswith("#") and name.endswith("#"))
    )
