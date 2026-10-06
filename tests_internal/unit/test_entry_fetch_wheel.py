import hashlib
import json
import runpy
from collections.abc import Callable
from pathlib import Path

import pytest

import apitest

SCRIPT = Path(apitest.__file__).parent / "assets/entry/repo2test/scripts/fetch_wheel.py"
WHEEL = "apitest-9.9.9-py3-none-any.whl"
PAYLOAD = b"wheel bytes"


def _fetch() -> Callable[[str, Path], dict[str, str]]:
    # run_path leaves no bytecode cache beside the script, which ships as an asset.
    fetch: Callable[[str, Path], dict[str, str]] = runpy.run_path(str(SCRIPT))["fetch"]
    return fetch


def _release(root: Path, digest: str | None, assets: tuple[str, ...] = (WHEEL,)) -> str:
    """A release served from disk; returns the URL of its description."""
    remote = root / "remote"
    remote.mkdir()
    listed = []
    for name in assets:
        (remote / name).write_bytes(PAYLOAD)
        asset = {"name": name, "browser_download_url": (remote / name).as_uri()}
        if digest is not None:
            asset["digest"] = digest
        listed.append(asset)
    description = remote / "latest.json"
    description.write_text(json.dumps({"tag_name": "v9.9.9", "assets": listed}))
    return description.as_uri()


def test_fetch_saves_the_wheel_the_release_publishes(tmp_path: Path) -> None:
    published = hashlib.sha256(PAYLOAD).hexdigest()
    url = _release(tmp_path, f"sha256:{published}", assets=("notes.txt", WHEEL))

    result = _fetch()(url, tmp_path / "download")

    assert (tmp_path / "download" / WHEEL).read_bytes() == PAYLOAD
    assert result == {
        "wheel": str(tmp_path / "download" / WHEEL),
        "version": "9.9.9",
        "sha256": published,
    }


def test_fetch_keeps_nothing_when_the_bytes_differ_from_the_published_digest(
    tmp_path: Path,
) -> None:
    url = _release(tmp_path, "sha256:" + "0" * 64)

    with pytest.raises(SystemExit) as stopped:
        _fetch()(url, tmp_path / "download")

    assert "SHA-256" in str(stopped.value)
    assert not list((tmp_path / "download").glob("*"))


def test_fetch_refuses_a_wheel_the_release_publishes_no_digest_for(tmp_path: Path) -> None:
    url = _release(tmp_path, None)

    with pytest.raises(SystemExit) as stopped:
        _fetch()(url, tmp_path / "download")

    assert "SHA-256" in str(stopped.value)
    assert not (tmp_path / "download" / WHEEL).exists()


def test_fetch_stops_when_the_release_has_no_runner_wheel(tmp_path: Path) -> None:
    url = _release(tmp_path, "sha256:" + "0" * 64, assets=("notes.txt",))

    with pytest.raises(SystemExit) as stopped:
        _fetch()(url, tmp_path / "download")

    assert "wheel" in str(stopped.value)


def test_fetch_reports_an_unreachable_release_as_a_message(tmp_path: Path) -> None:
    missing = (tmp_path / "nowhere.json").as_uri()

    with pytest.raises(SystemExit) as stopped:
        _fetch()(missing, tmp_path / "download")

    assert isinstance(stopped.value.code, str) and "release" in stopped.value.code
