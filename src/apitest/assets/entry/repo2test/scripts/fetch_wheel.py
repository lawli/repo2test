"""Download the latest released runner wheel and check it against the published digest."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

# The only source: the releases of the repository this entry was installed from.
RELEASE = "https://api.github.com/repos/lawli/repo2test/releases/latest"


def fetch(release_url: str, dest: Path) -> dict[str, str]:
    try:
        with urllib.request.urlopen(release_url, timeout=30) as response:
            release = json.load(response)
        wheels = [
            asset
            for asset in release["assets"]
            if asset["name"].startswith("apitest-") and asset["name"].endswith(".whl")
        ]
        if len(wheels) != 1:
            raise SystemExit("The latest release does not publish exactly one runner wheel")
        asset = wheels[0]
        algorithm, _, published = (asset.get("digest") or "").partition(":")
        if algorithm != "sha256" or not published:
            raise SystemExit("The latest release publishes no SHA-256 for its runner wheel")
        with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as response:
            data = response.read()
    except OSError as exc:
        raise SystemExit(f"Could not download the release wheel: {exc}") from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != published:
        raise SystemExit(f"Downloaded wheel has SHA-256 {actual}; the release has {published}")
    dest.mkdir(parents=True, exist_ok=True)
    wheel = dest / asset["name"]
    wheel.write_bytes(data)
    return {
        "wheel": str(wheel),
        "version": release["tag_name"].removeprefix("v"),
        "sha256": actual,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, required=True)
    print(json.dumps(fetch(RELEASE, parser.parse_args().dest)))
