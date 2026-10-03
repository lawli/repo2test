import json
import os
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from apitest.bundle import Bundle

PROJECT = Path(__file__).resolve().parents[2]


def command(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    folder = tmp_path_factory.mktemp("wheel")
    command(["uv", "build", "--wheel", "--offline", "--out-dir", str(folder)], PROJECT)
    return next(folder.glob("*.whl"))


def create_workspace(tmp_path, wheel):
    business = tmp_path / "business"
    business.mkdir()
    command(["git", "init", "-q"], business)
    command(["git", "config", "user.name", "Test"], business)
    command(["git", "config", "user.email", "test@example.invalid"], business)
    (business / "api.yaml").write_text("# Contract fixture\n")
    command(["git", "add", "."], business)
    command(["git", "commit", "-qm", "source"], business)
    workspace = tmp_path / "business-e2e"
    command(
        [
            sys.executable,
            "-m",
            "apitest.cli",
            "workspace",
            "init",
            str(workspace),
            "--target",
            str(business),
            "--runner-wheel",
            str(wheel),
            "--ref",
            "HEAD",
            "--runner-owner",
            "@runner-maintainers",
            "--ci",
            "github",
            "--offline",
        ],
        tmp_path,
    )
    return workspace, command(["git", "rev-parse", "HEAD"], business).strip()


def test_cloned_workspace_installs_runs_reports_and_routes_without_framework_checkout(
    tmp_path,
    wheel,
):
    original, source = create_workspace(tmp_path, wheel)
    # Only distribute files from the generated test repo. Its source repo path is now absent.
    root = tmp_path / "clean-machine" / "business-e2e"
    command(["git", "init", "-q"], original)
    command(["git", "config", "user.name", "Test"], original)
    command(["git", "config", "user.email", "test@example.invalid"], original)
    command(["git", "add", "."], original)
    command(["git", "commit", "-qm", "workspace"], original)
    command(["git", "clone", "--config", "core.autocrlf=true", str(original), str(root)], tmp_path)
    assert (root / "tests/.gitkeep").is_file()
    assert (root / "coverage/.gitkeep").is_file()
    assert (root / "defects/.gitkeep").is_file()
    assert b"\r\n" in (root / "README.md").read_bytes()
    assert Bundle.read(wheel).asset_errors(root) == []
    command([sys.executable, "-m", "apitest.cli", "--workspace", str(root), "validate"], root)
    command(["uv", "sync", "--locked", "--offline"], root)
    executable = root / ".venv/bin/apitest"
    installed = command(
        [
            str(root / ".venv/bin/python"),
            "-c",
            "import apitest; print(apitest.__file__)",
        ],
        root,
    ).strip()
    assert str(root / ".venv") in installed
    assert Bundle.read(wheel).provenance["source_commit"]
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"UP"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command(
            [
                str(executable),
                "workspace",
                "configure",
                "--service",
                "orders",
                "--url",
                f"http://127.0.0.1:{server.server_port}",
                "--environment",
                "non-production",
            ],
            root,
        )
        rows = []
        suite = root / "tests/orders"
        suite.mkdir()
        for name, expectation in [
            ("happy", "confirmed"),
            ("pending", "pending"),
            ("blocked", "confirmed"),
        ]:
            key = f"orders|GET|/health|happy|{name}"
            case = {
                "schema": "v1",
                "name": name,
                "service": "orders",
                "source_commit": source,
                "expectation": expectation,
                "covers": [{"key": key}],
                "steps": [
                    {
                        "name": "health",
                        "request": {"method": "GET", "path": "/health"},
                        "assert": {"status": 200, "json": {"status": "UP"}},
                    }
                ],
            }
            if name == "blocked":
                case["verify"] = {"redis": [{"key": "unconfigured", "exists": True}]}
            (suite / f"case_{name}--abc123.yaml").write_text(yaml.safe_dump(case))
            rows.append(
                {
                    "key": key,
                    "source_commit": source,
                    "expectation": expectation,
                    "status": "authored",
                    "evidence": [{"file": "api.yaml", "line": 1, "kind": "requirement"}],
                }
            )
        (root / "coverage/health.yaml").write_text(
            yaml.safe_dump(
                {
                    "service": "orders",
                    "method": "GET",
                    "path": "/health",
                    "rows": rows,
                }
            )
        )
        command([str(executable), "validate", "--profile", "local"], suite)
        assert seen == []  # generation/validation must not touch the service
        result = subprocess.run(
            [
                str(executable),
                "run",
                "--service",
                "orders",
                "--profile",
                "local",
                "--report",
                "both",
            ],
            cwd=suite,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode != 0, result.stdout
        summary = json.loads((root / "reports/summary.json").read_text())
        assert summary["totals"]["pass"] == 2
        assert summary["totals"]["blocked"] == 1
        assert summary["complete"] is False
        assert seen == ["/health", "/health"]
        junit = ET.parse(root / "reports/junit.xml")
        assert len(junit.findall(".//failure")) == 1
        view = json.loads(
            command(
                [
                    str(executable),
                    "coverage",
                    "--results",
                    str(root / "reports/summary.json"),
                ],
                root,
            )
        )
        assert view["verified"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    for host in ("codex", "claude"):
        install = tmp_path / f"{host}-skills"
        command(
            [sys.executable, "tools/install_entry.py", "--host", host, "--directory", str(install)],
            root,
        )
        entry = install / "repo2test"
        assert entry.is_dir()
        located = json.loads(
            command(
                [
                    sys.executable,
                    str(entry / "scripts/locate.py"),
                    "--start",
                    str(suite),
                ],
                root,
            )
        )
        assert located["workspace"] == str(root)
        assert (
            Path(located["skill"]).read_bytes()
            == (root / ".claude/skills/repo2test-workspace/SKILL.md").read_bytes()
        )


def test_upgrade_uses_supplied_wheel_assets_and_preserves_anchors(tmp_path, wheel):
    root, source = create_workspace(tmp_path, wheel)
    # Reproduce a legacy checkout with inline runner metadata and no Git attributes.
    marker = root / "repo2test.toml"
    marker.write_text(marker.read_text() + (root / "runner.toml").read_text().replace(
        "[runner]", "[runner] # legacy metadata"
    ))
    (root / "runner.toml").unlink()
    (root / ".gitattributes").write_text("*.custom -text\n")
    owners = root / ".github/CODEOWNERS"
    owners.write_text("/repo2test.toml @runner-maintainers\n/vendor/*.whl @runner-maintainers\n"
                      "/tests/ @qa\n")
    (root / "defects/.gitkeep").unlink()
    case = root / "tests/case_manual.yaml"
    original = (
        "# Preserve this rationale and compact layout.\r\n"
        "schema: v1\r\nname: manual\r\nservice: orders\r\n"
        f"source_commit: '{source}'\r\n"
        "steps:\r\n  - name: health\r\n"
        "    request: {method: GET, path: /health}\r\n"
        "    assert: {status: 200} # Contract-defined response\r\n"
    ).encode()
    case.write_bytes(original)
    next_source = tmp_path / "next-runner"
    next_source.mkdir()
    shutil.copytree(
        PROJECT / "src", next_source / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy2(PROJECT / "hatch_build.py", next_source / "hatch_build.py")
    shutil.copy2(PROJECT / "LICENSE", next_source / "LICENSE")
    import re

    project = (PROJECT / "pyproject.toml").read_text()
    major, minor, patch = re.search(r'(?m)^version = "(\d+)\.(\d+)\.(\d+)"', project).groups()
    next_version = f"{major}.{minor}.{int(patch) + 1}"
    project = re.sub(r'(?m)^version = "[^"]+"', f'version = "{next_version}"', project, count=1)
    (next_source / "pyproject.toml").write_text(project)
    skill_path = next_source / "src/apitest/assets/skills/repo2test-workspace/SKILL.md"
    skill_path.write_text(skill_path.read_text() + "\nVersion-specific upgrade fixture.\n")
    notes = next_source / "src/apitest/assets/upgrade-notes.md"
    notes.write_text(notes.read_text() + f"\n## {next_version}\n\n- Fixture note about mutates.\n")
    command(["uv", "build", "--wheel", "--offline"], next_source)
    new_wheel = next(next_source.glob("dist/*.whl"))
    output = command(
        [
            sys.executable,
            "-m",
            "apitest.cli",
            "--workspace",
            str(root),
            "workspace",
            "upgrade",
            "--runner-wheel",
            str(new_wheel),
            "--offline",
            "--migrate",
        ],
        tmp_path,
    )
    command(["uv", "sync", "--locked", "--offline"], root)
    version = command(
        [
            str(root / ".venv/bin/python"),
            "-c",
            "import apitest; print(apitest.__version__)",
        ],
        root,
    )
    assert version.strip() == next_version
    assert "compatibility" in output and "Fixture note about mutates" in output
    assert "## 0.2.1" not in output
    assert [p.name for p in (root / "vendor").glob("*.whl")] == [new_wheel.name]
    assert "[runner]" not in marker.read_text()
    assert (root / "runner.toml").is_file() and (root / "defects/.gitkeep").is_file()
    assert "*.custom -text" in (root / ".gitattributes").read_text()
    assert "/repo2test.toml" not in owners.read_text()
    assert "/runner.toml @runner-maintainers" in owners.read_text()
    assert "/tests/ @qa" in owners.read_text()
    from apitest.workspace import Workspace

    assert Workspace.load(root).source_commit == source
    assert case.read_bytes() == original
    assert (
        root / ".claude/skills/repo2test-workspace/SKILL.md"
    ).read_text() == skill_path.read_text()


@pytest.mark.parametrize(
    "relative",
    [
        ".claude/skills/repo2test-workspace/SKILL.md",
        ".claude/skills/repo2test-workspace/references/authoring.md",
        ".claude/skills/repo2test-workspace/references/extra.md",
        "tools/entry/repo2test/SKILL.md",
        "tools/install_entry.py",
    ],
)
def test_pinned_asset_drift_fails_both_validate_and_doctor(tmp_path, wheel, relative):
    root, _ = create_workspace(tmp_path, wheel)
    path = root / relative
    path.write_bytes((path.read_bytes() if path.exists() else b"") + b"\n# local drift\n")
    for args in (["validate"], ["workspace", "doctor"]):
        result = subprocess.run(
            [sys.executable, "-m", "apitest.cli", "--workspace", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert relative in result.stdout + result.stderr


def test_init_requires_explicit_reference_and_records_estimates(tmp_path, wheel):
    root, source = create_workspace(tmp_path, wheel)
    destination = tmp_path / "estimated-e2e"
    args = [
        sys.executable,
        "-m",
        "apitest.cli",
        "workspace",
        "init",
        str(destination),
        "--target",
        str(tmp_path / "business"),
        "--runner-wheel",
        str(wheel),
        "--runner-owner",
        "@qa",
        "--ci",
        "github",
        "--offline",
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "--ref" in result.stderr
    assert not destination.exists()
    command([*args, "--ref", "HEAD", "--estimated-because", "Deployment ref unavailable"], tmp_path)
    from apitest.workspace import Workspace

    reference = Workspace.load(destination).config["reference"]
    assert reference["estimated"] is True and reference["commit"] == source
    assert reference["evidence"] == "Deployment ref unavailable"
    assert Workspace.load(root).config["reference"]["estimated"] is False


def cli_run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "apitest.cli", "--workspace", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_head_is_recorded_as_its_commit_and_ignores_are_rooted(tmp_path, wheel):
    from apitest.workspace import Workspace

    root, source = create_workspace(tmp_path, wheel)
    reference = Workspace.load(root).config["reference"]
    assert reference["ref"] == reference["commit"] == source
    ignores = (root / ".gitignore").read_text().splitlines()
    assert "/reports/" in ignores and "/.source/" in ignores


def test_reference_update_lists_rows_whose_evidence_changed(tmp_path, wheel):
    root, source = create_workspace(tmp_path, wheel)
    business = tmp_path / "business"
    (business / "Other.java").write_text("class Other {}\n")
    command(["git", "add", "."], business)
    command(["git", "commit", "-qm", "other"], business)
    rows = [
        {
            "key": "orders|GET|/orders|happy|a",
            "source_commit": source,
            "evidence": [{"file": "api.yaml", "line": 1, "kind": "requirement"}],
        },
        {
            "key": "orders|GET|/orders|happy|b",
            "source_commit": source,
            "evidence": [{"file": "Other.java", "line": 1}],
        },
        {
            "key": "orders|GET|/orders|happy|c",
            "source_commit": source,
            "evidence": [{"file": "Stable.java", "line": 1}],
        },
    ]
    endpoint = {"service": "orders", "method": "GET", "path": "/orders", "rows": rows}
    (root / "coverage/orders.yaml").write_text(yaml.safe_dump(endpoint))
    (business / "api.yaml").write_text("# Contract fixture, revised\n")
    command(["git", "commit", "-qam", "contract"], business)
    result = cli_run(root, "workspace", "reference", "--ref", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    changed = {r["key"]: r["changed_evidence"] for r in report["review"]}
    assert changed == {
        "orders|GET|/orders|happy|a": ["api.yaml"],
        "orders|GET|/orders|happy|b": ["Other.java"],
    }


def test_doctor_reports_a_half_upgraded_layout(tmp_path, wheel):
    root, _ = create_workspace(tmp_path, wheel)
    assert cli_run(root, "workspace", "doctor").returncode == 0
    (root / ".gitattributes").write_text("*.custom -text\n")
    (root / "defects/.gitkeep").unlink()
    result = cli_run(root, "workspace", "doctor")
    assert result.returncode == 2
    assert "incomplete workspace layout" in result.stdout
    assert ".gitattributes" in result.stdout and "defects/.gitkeep" in result.stdout


def test_init_rejects_a_wheel_without_provenance_cleanly(tmp_path, wheel):
    import zipfile

    stripped = tmp_path / wheel.name
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(stripped, "w") as target:
        for item in source.infolist():
            if item.filename != "apitest/build_info.json":
                target.writestr(item, source.read(item))
    business = tmp_path / "business"
    business.mkdir()
    command(["git", "init", "-q"], business)
    command(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@e.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        business,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "apitest.cli",
            "workspace",
            "init",
            str(tmp_path / "ws"),
            "--target",
            str(business),
            "--ref",
            "HEAD",
            "--runner-wheel",
            str(stripped),
            "--runner-owner",
            "@qa",
            "--ci",
            "github",
            "--offline",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Unusable runner wheel" in result.stderr and "Traceback" not in result.stderr


def test_doctor_sync_failure_is_reported_without_generation(tmp_path, wheel, monkeypatch):
    root, _ = create_workspace(tmp_path, wheel)
    empty_cache = tmp_path / "empty-uv-cache"
    empty_cache.mkdir()
    shutil.rmtree(root / ".venv", ignore_errors=True)
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(empty_cache))
    result = cli_run(root, "workspace", "doctor", "--sync")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["synced"] is False
    assert any("dependency synchronization failed" in p for p in report["problems"])
    assert any("uv cache dir" in p for p in report["problems"])
    assert not list((root / "tests").rglob("case_*.yaml"))


def test_doctor_accepts_populated_directories_and_team_owned_marker_rules(tmp_path, wheel):
    root, source = create_workspace(tmp_path, wheel)
    (root / "tests/.gitkeep").unlink()
    (root / "tests/case_ok.yaml").write_text("placeholder: true\n")
    owners = root / ".github/CODEOWNERS"
    owners.write_text(owners.read_text() + "/repo2test.toml @qa-leads\n")
    result = cli_run(root, "workspace", "doctor")
    assert result.returncode == 0, result.stdout + result.stderr


def test_doctor_requires_a_placeholder_when_only_empty_directories_remain(tmp_path, wheel):
    root, _ = create_workspace(tmp_path, wheel)
    (root / "defects/.gitkeep").unlink()
    (root / "defects/archive").mkdir()
    result = cli_run(root, "workspace", "doctor")
    assert result.returncode == 2 and "defects/.gitkeep" in result.stdout


def test_reference_update_lists_rows_whose_evidence_file_was_renamed(tmp_path, wheel):
    root, source = create_workspace(tmp_path, wheel)
    business = tmp_path / "business"
    row = {
        "key": "orders|GET|/orders|happy|a",
        "source_commit": source,
        "evidence": [{"file": "api.yaml", "line": 1, "kind": "requirement"}],
    }
    endpoint = {"service": "orders", "method": "GET", "path": "/orders", "rows": [row]}
    (root / "coverage/orders.yaml").write_text(yaml.safe_dump(endpoint))
    command(["git", "mv", "api.yaml", "contract.yaml"], business)
    command(["git", "commit", "-qm", "rename contract"], business)
    result = cli_run(root, "workspace", "reference", "--ref", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    review = json.loads(result.stdout)["review"]
    assert [r["changed_evidence"] for r in review] == [["api.yaml"]]

    # The row is still anchored at its original commit, so a later update keeps it listed.
    (business / "Other.java").write_text("class Other {}\n")
    command(["git", "add", "."], business)
    command(["git", "commit", "-qm", "unrelated"], business)
    result = cli_run(root, "workspace", "reference", "--ref", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    review = json.loads(result.stdout)["review"]
    assert [(r["anchor"], r["changed_evidence"]) for r in review] == [(source, ["api.yaml"])]


def test_reference_update_lists_every_row_whose_anchor_cannot_be_compared(tmp_path, wheel):
    root, _ = create_workspace(tmp_path, wheel)
    rows = [
        {
            "key": f"orders|GET|/orders|happy|{name}",
            "source_commit": anchor,
            "evidence": [{"file": "api.yaml", "line": 1, "kind": "requirement"}],
        }
        for name, anchor in (
            ("missing", "0" * 40),
            ("option", "--output=diff.txt"),
            ("symbolic", "HEAD"),
        )
    ]
    endpoint = {"service": "orders", "method": "GET", "path": "/orders", "rows": rows}
    (root / "coverage/orders.yaml").write_text(yaml.safe_dump(endpoint))
    result = cli_run(root, "workspace", "reference", "--ref", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert [r["changed_evidence"] for r in report["review"]] == [["api.yaml"]] * 3
    assert "anchors unavailable" in report["comparison"]
    assert not list(tmp_path.rglob("diff.txt"))


def test_run_refuses_python_that_pytest_would_import_from_the_test_tree(tmp_path, wheel):
    root, _ = create_workspace(tmp_path, wheel)
    sentinel = tmp_path / "imported"
    touch = f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
    (root / "tests/shop/_helpers").mkdir(parents=True)
    (root / "tests/shop/_helpers/login.py").write_text("def login(ctx):\n    pass\n")
    (root / "tests/shop/__init__.py").write_text(touch)
    (root / "tests/conftest.py").write_text(touch)
    (root / "tests/shop/case_ok.yaml").write_text(
        "schema: v1\nname: ok\nservice: shop\nsteps:\n"
        "  - name: read\n    request: {method: GET, path: /read}\n    assert: {status: 200}\n"
    )
    result = cli_run(root, "run", "--id", "shop")
    assert result.returncode == 2 and not sentinel.exists(), result.stdout + result.stderr
    for name in ("tests/shop/__init__.py", "tests/conftest.py"):
        assert name in result.stderr and name in cli_run(root, "validate").stderr
    assert "login.py" not in result.stderr

    (root / "tests/shop/__init__.py").unlink()
    (root / "tests/conftest.py").unlink()
    (root / "conftest.py").write_text(touch)
    result = cli_run(root, "run", "--id", "shop", "--dry-run")
    assert not sentinel.exists(), result.stdout + result.stderr

    # A package __init__ outside the test tree, or a root module shadowing one pytest
    # imports, is still never imported.
    (root / "conftest.py").unlink()
    (root / "__init__.py").write_text(touch)
    (root / "code.py").write_text(touch)
    (root / "html.py").write_text(touch)
    for extra in ([], ["--parallel", "2", "--report", "both"]):
        result = cli_run(root, "run", "--id", "shop", *extra)
        assert not sentinel.exists(), result.stdout + result.stderr
    (root / "code.py").unlink()
    (root / "html.py").unlink()

    # A symlinked suite would run unvalidated, so it is rejected.
    linked = tmp_path / "linked-suite"
    linked.mkdir()
    (linked / "__init__.py").write_text(touch)
    shutil.copy2(root / "tests/shop/case_ok.yaml", linked / "case_ok.yaml")
    (root / "tests/linked").symlink_to(linked, target_is_directory=True)
    result = cli_run(root, "run", "--id", "linked")
    assert result.returncode == 2 and not sentinel.exists(), result.stdout + result.stderr
    for output in (result.stderr, cli_run(root, "validate").stderr):
        assert "tests/linked: symlinked directories are not validated" in output
    assert "symlinked directories" in cli_run(root, "workspace", "doctor").stdout


def test_init_failing_at_uv_lock_leaves_the_destination_as_it_was(tmp_path, wheel):
    business = tmp_path / "business"
    business.mkdir()
    command(["git", "init", "-q"], business)
    command(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e.invalid", "commit", "-q",
         "--allow-empty", "-m", "x"],
        business,
    )
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "uv").write_text("#!/bin/sh\necho 'no package index' >&2\nexit 1\n")
    (fake / "uv").chmod(0o755)
    no_index = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    destination = tmp_path / "business-e2e"
    args = [
        sys.executable, "-m", "apitest.cli", "workspace", "init", str(destination),
        "--target", str(business), "--runner-wheel", str(wheel), "--ref", "HEAD",
        "--runner-owner", "@qa", "--ci", "github", "--offline",
    ]
    failed = subprocess.run(args, capture_output=True, text=True, timeout=60, env=no_index)
    assert failed.returncode == 1, failed.stderr
    assert "no package index" in failed.stderr and "rerun" in failed.stderr
    assert not destination.exists()
    destination.mkdir()
    failed = subprocess.run(args, capture_output=True, text=True, timeout=60, env=no_index)
    assert failed.returncode == 1 and list(destination.iterdir()) == []
    command(args, tmp_path)  # the same command now succeeds on the same destination
    assert (destination / "uv.lock").is_file()
