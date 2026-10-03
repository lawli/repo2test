# Fixtures for tests_internal live here.
import pytest

from apitest.taint import reset_taint


@pytest.fixture(autouse=True)
def _reset_taint_between_tests():
    reset_taint()
    yield
    reset_taint()


def _has_mysql() -> bool:
    import os

    return bool(os.environ.get("TEST_MYSQL_DSN"))


pytest_plugins = ["pytester"]


@pytest.fixture
def install_pinned_runner():
    """Small distribution fixture with real shipped assets and wheel provenance."""
    import hashlib
    import json
    import zipfile
    from pathlib import Path

    import apitest
    from apitest.bundle import Bundle

    def install(root):
        wheel = root / "vendor/apitest-0.2.0-py3-none-any.whl"
        wheel.parent.mkdir(exist_ok=True)
        assets = Path(apitest.__file__).parent / "assets"
        provenance = {"source_commit": "f" * 40, "source_sha256": "e" * 64}
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("apitest-0.2.0.dist-info/METADATA", "Name: apitest\nVersion: 0.2.0\n")
            archive.writestr("apitest/build_info.json", json.dumps(provenance))
            for path in assets.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, "apitest/assets/" + path.relative_to(assets).as_posix())
        Bundle.read(wheel).install_assets(root)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        marker = root / "repo2test.toml"
        text = marker.read_text() if marker.exists() else ""
        text += '\n[paths]\nskill = ".claude/skills/repo2test-workspace/SKILL.md"\n[runner]\n'
        for key, value in {
            "wheel": wheel.relative_to(root).as_posix(),
            "version": "0.2.0",
            "sha256": digest,
            **provenance,
        }.items():
            text += f"{key} = {json.dumps(value)}\n"
        marker.write_text(text)
        return wheel

    return install
