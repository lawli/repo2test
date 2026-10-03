import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from apitest.cli import _load_dotenv, cli
from apitest.coverage import coverage_view, reconcile
from apitest.validation import migrate_case, validate_workspace
from apitest.workspace import Workspace
from apitest.workspace_cli import source_checkout

SOURCE = "a" * 40
KEY = "orders|GET|/orders/{id}|happy|existing"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def init_git(root):
    root.mkdir(exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")


@pytest.fixture
def workspace(tmp_path, install_pinned_runner):
    root = tmp_path / "orders-e2e"
    root.mkdir()
    for name in ("tests/orders", "coverage", "profiles"):
        (root / name).mkdir(parents=True)
    (root / "repo2test.toml").write_text(
        f'[reference]\ncommit = "{SOURCE}"\n[target]\npath = "../orders"\n'
    )
    install_pinned_runner(root)
    row = {
        "key": KEY,
        "source_commit": SOURCE,
        "status": "authored",
        "evidence": [{"file": "api.yaml", "line": 10, "kind": "requirement"}],
    }
    endpoint = {
        "service": "orders",
        "method": "GET",
        "path": "/orders/{id}",
        "evidence": [{"file": "Orders.java", "line": 2, "kind": "route"}],
        "rows": [row],
    }
    (root / "coverage/orders.yaml").write_text(yaml.safe_dump(endpoint))
    case = {
        "schema": "v1",
        "name": "existing--abc123",
        "service": "orders",
        "source_commit": SOURCE,
        "covers": [{"key": KEY}],
        "steps": [
            {
                "name": "read",
                "request": {"method": "GET", "path": "/orders/1"},
                "assert": {"status": 200},
            }
        ],
    }
    (root / "tests/orders/case_existing--abc123.yaml").write_text(yaml.safe_dump(case))
    return Workspace.load(root)


def case_path(ws):
    return next(ws.path("tests").rglob("case_*.yaml"))


@pytest.mark.parametrize(
    "ref",
    [
        "PROJ-123",
        "#123",
        "owner/repo#123",
        "https://tracker.invalid/issues/123",
        "defects/order--abc123.md",
    ],
)
def test_workspace_accepts_tracker_ids_urls_and_local_defects(workspace, ref):
    (workspace.root / "defects").mkdir()
    (workspace.root / "defects/order--abc123.md").write_text("Expected response differs\n")
    path = case_path(workspace)
    case = yaml.safe_load(path.read_text())
    case["known_defects"] = [{"ref": ref, "step": "read"}]
    path.write_text(yaml.safe_dump(case))
    assert validate_workspace(workspace) == []
    result = CliRunner().invoke(cli, ["--workspace", str(workspace.root), "validate"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "ref", ["defects/missing.md", "defects/../../outside.md", "https://", "arbitrary typo"]
)
def test_workspace_rejects_invalid_defect_references(workspace, ref):
    path = case_path(workspace)
    case = yaml.safe_load(path.read_text())
    case["known_defects"] = [{"ref": ref, "step": "read"}]
    path.write_text(yaml.safe_dump(case))
    assert any("defect" in e for e in validate_workspace(workspace))


def test_indexed_data_keys_and_write_sql_are_checked_offline(workspace):
    path = case_path(workspace)
    case = yaml.safe_load(path.read_text())
    case["headers"] = {"x-user": "${ctx.profile.test_data['account']}"}
    path.write_text(yaml.safe_dump(case))
    assert any("undeclared test_data key account" in e for e in validate_workspace(workspace))
    workspace.config["test_data"] = {"keys": ["account"]}
    assert validate_workspace(workspace) == []
    case["verify"] = {"db": [{"sql": "TRUNCATE TABLE t", "expect_count": 0}]}
    path.write_text(yaml.safe_dump(case))
    assert any("verify.db.sql" in e for e in validate_workspace(workspace))


@pytest.mark.parametrize("runner", [None, {}, [], {"wheel": "missing.whl"}])
def test_runner_metadata_is_mandatory_for_validation(workspace, runner):
    if runner is None:
        workspace.config.pop("runner")
    else:
        workspace.config["runner"] = runner
    assert any("runner metadata is required" in e for e in validate_workspace(workspace))


@pytest.mark.parametrize("field", ["version", "source_commit", "source_sha256"])
def test_runner_provenance_must_match_wheel(workspace, field):
    workspace.config["runner"][field] = "wrong"
    assert any(f"runner.{field}" in e for e in validate_workspace(workspace))


def test_invalid_wheel_reports_validation_error_instead_of_crashing(workspace):
    import hashlib

    runner = workspace.config["runner"]
    wheel = workspace.root / runner["wheel"]
    wheel.write_bytes(b"not a zip")
    runner["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert any("invalid pinned runner distribution" in e for e in validate_workspace(workspace))


@pytest.mark.parametrize("name", [".DS_Store", "Thumbs.db", "desktop.ini"])
def test_os_metadata_is_not_executable_asset_drift(workspace, name):
    folder = workspace.root / ".claude/skills/repo2test-workspace"
    (folder / name).write_bytes(b"OS metadata")
    assert workspace.runner_problems() == []
    (folder / "new-instructions.md").write_text("Unexpected instructions")
    assert any("new-instructions.md" in e for e in workspace.runner_problems())


def test_external_runner_metadata_rejects_duplicate_definitions(workspace):
    (workspace.root / "runner.toml").write_text('[runner]\nversion = "0.2.2"\n')
    with pytest.raises(ValueError, match="duplicated"):
        Workspace.load(workspace.root)


@pytest.mark.parametrize("original_newline, changed_newline", [(b"\n", b"\r\n"), (b"\r\n", b"\n")])
def test_lock_changes_do_not_hide_newline_only_edits(workspace, original_newline, changed_newline):
    path = case_path(workspace)
    content = path.read_bytes()
    path.write_bytes(content.replace(b"\n", original_newline))
    init_git(workspace.root)
    git(workspace.root, "config", "core.autocrlf", "false")
    git(workspace.root, "add", ".")
    git(workspace.root, "commit", "-qm", "base")
    workspace.config["reference"]["commit"] = "b" * 40
    path.write_bytes(content.replace(b"\n", changed_newline))
    (workspace.root / "uv.lock").write_text("version = 1\n")
    assert any("changed case has stale" in e for e in validate_workspace(workspace, base="HEAD"))


def test_migration_exception_requires_exact_replay_and_unchanged_anchors(workspace, monkeypatch):
    path = case_path(workspace)
    original = path.read_text()
    init_git(workspace.root)
    git(workspace.root, "add", ".")
    git(workspace.root, "commit", "-qm", "base")
    workspace.config["reference"]["commit"] = "b" * 40
    (workspace.root / "uv.lock").write_text("version = 1\n")
    # Simulate a future deterministic migration of a retired field.
    migrated = original + "parallel_safe: false\n"
    monkeypatch.setattr("apitest.validation.migrate_case", lambda _: migrated)
    path.write_text(migrated)
    assert validate_workspace(workspace, base="HEAD") == []
    path.write_text(migrated + "# human edit\n")
    assert any("changed case has stale" in e for e in validate_workspace(workspace, base="HEAD"))
    migrated = migrated.replace(SOURCE, "c" * 40)
    path.write_text(migrated)
    assert any("changed case has stale" in e for e in validate_workspace(workspace, base="HEAD"))


def test_upgrade_rejects_changed_bytes_at_same_version_before_any_workspace_mutation(
    workspace, monkeypatch
):
    import zipfile

    from scripts.verify_host_authoring import snapshot

    wheel = workspace.root / workspace.config["runner"]["wheel"]
    changed = workspace.root.parent / wheel.name
    changed.write_bytes(wheel.read_bytes())
    with zipfile.ZipFile(changed, "a") as archive:
        archive.writestr("apitest/new-code.py", "# changed release\n")
    before = snapshot(workspace.root)
    monkeypatch.chdir(workspace.root)
    result = CliRunner().invoke(cli, ["workspace", "upgrade", "--runner-wheel", str(changed)])
    assert result.exit_code != 0 and "new distribution version" in result.output
    assert before == snapshot(workspace.root)


def test_validation_never_imports_helpers_and_checks_arguments(workspace):
    path = case_path(workspace)
    helpers = path.parent / "_helpers"
    helpers.mkdir()
    (helpers / "probe.py").write_text(
        'raise RuntimeError("must not execute during validation")\n'
        "def inspect_order(http, order_id): pass\n"
    )
    data = yaml.safe_load(path.read_text())
    data["verify"] = {"python": [{"call": "helpers.probe.inspect_order", "args": {"order_id": 1}}]}
    path.write_text(yaml.safe_dump(data))
    assert validate_workspace(workspace) == []
    data["verify"]["python"][0]["args"] = {"wrong": 1}
    path.write_text(yaml.safe_dump(data))
    assert any(
        "missing=['order_id']" in e and "unknown=['wrong']" in e
        for e in validate_workspace(workspace)
    )


@pytest.mark.parametrize("rule", ["fixture", "helper", "matcher", "config-key", "defect-location"])
def test_cli_rejects_invalid_authoring_inputs(workspace, rule):
    path = case_path(workspace)
    case = yaml.safe_load(path.read_text())
    if rule == "fixture":
        case["steps"][0]["request"]["files"] = {"upload": "missing.csv"}
        message = "fixture missing"
    elif rule == "helper":
        case["verify"] = {"python": [{"call": "helpers.missing.check"}]}
        message = "missing or ambiguous"
    elif rule == "matcher":
        case["steps"][0]["assert"]["text"] = "@regex:["
        message = "malformed matcher"
    elif rule == "config-key":
        case["steps"][0]["request"]["params"] = {"id": "${ctx.profile.test_data.undeclared}"}
        message = "undeclared test_data"
    else:
        case["known_defects"] = [{"ref": "BUG-1", "step": "read", "at": "json.absent"}]
        message = "unknown assertion"
    path.write_text(yaml.safe_dump(case))
    result = CliRunner().invoke(cli, ["--workspace", str(workspace.root), "validate"])
    assert result.exit_code == 2 and message in result.output, result.output


def _checkout(workspace, files):
    """The source checkout of the reference commit, holding the given files."""
    root = workspace.root / ".source" / SOURCE
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


# The fixture inventory cites Orders.java:2 (route) and api.yaml:10 (requirement).
CITED = {"Orders.java": "class Orders {\n  @GetMapping\n}\n", "api.yaml": "x: 1\n" * 10}


def test_evidence_found_in_the_source_checkout_is_accepted(workspace):
    _checkout(workspace, CITED)
    assert validate_workspace(workspace) == []


def test_evidence_is_not_checked_without_a_source_checkout(workspace):
    assert not (workspace.root / ".source").exists()
    assert validate_workspace(workspace) == []


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"Orders.java": CITED["Orders.java"]}, "api.yaml:10 names a file the source checkout"),
        ({**CITED, "api.yaml": "x: 1\n" * 9}, "api.yaml:10 is past the end of the file"),
        ({**CITED, "Orders.java": "class Orders {\n\n}\n"}, "Orders.java:2 is a blank line"),
    ],
)
def test_evidence_that_the_source_checkout_does_not_support_is_rejected(workspace, files, message):
    _checkout(workspace, files)
    errors = validate_workspace(workspace)
    assert len(errors) == 1 and message in errors[0], errors


@pytest.mark.parametrize("file", ["../outside.yaml", "/etc/hostname"])
def test_evidence_outside_the_source_checkout_is_rejected(workspace, file):
    _checkout(workspace, CITED)
    fragment = workspace.root / "coverage/orders.yaml"
    endpoint = yaml.safe_load(fragment.read_text())
    endpoint["rows"][0]["evidence"][0]["file"] = file
    fragment.write_text(yaml.safe_dump(endpoint))
    errors = validate_workspace(workspace)
    assert len(errors) == 1 and "is outside the source checkout" in errors[0], errors


def _reconcile_cli(workspace, source, *args):
    command = ["--workspace", str(workspace.root), "reconcile", "--source", str(source), *args]
    return CliRunner().invoke(cli, command)


@pytest.mark.parametrize("args", [["--pattern", "x"], ["--include", "*.ts"]])
def test_reconcile_pattern_and_include_must_be_given_together(workspace, tmp_path, args):
    source = tmp_path / "source"
    source.mkdir()
    result = _reconcile_cli(workspace, source, *args)
    assert result.exit_code == 2, result.output
    assert "--pattern" in result.output and "--include" in result.output


def test_reconcile_command_reports_gaps_found_by_a_supplied_pattern(workspace, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "orders.ts").write_text('router.get("/orders", list);\n')
    result = _reconcile_cli(workspace, source, "--pattern", r"router\.get\(", "--include", "*.ts")
    assert result.exit_code == 2, result.output
    report = json.loads(result.output)
    assert report["cross_check"] == "pattern"
    assert [g["file"] for g in report["discovery_gaps"]] == ["orders.ts"]


@pytest.mark.parametrize(("pattern", "exit_code"), [(r"route\(", 0), ("nothing", 2)])
def test_reconcile_command_fails_when_a_pattern_misses_cited_route_evidence(
    workspace, tmp_path, pattern, exit_code
):
    # The workspace inventory cites Orders.java:2 as route evidence.
    source = tmp_path / "source"
    source.mkdir()
    (source / "Orders.java").write_text("class Orders {\n  route('/orders/{id}')\n}\n")
    result = _reconcile_cli(workspace, source, "--pattern", pattern, "--include", "*.java")
    assert result.exit_code == exit_code, result.output
    report = json.loads(result.output)
    assert report["discovery_gaps"] == []
    unmatched = report["pattern"]["unmatched_evidence"]
    assert unmatched == ([] if exit_code == 0 else [{"file": "Orders.java", "line": 2}])


def test_reconcile_command_reports_an_invalid_pattern_without_a_traceback(workspace, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    result = _reconcile_cli(workspace, source, "--pattern", "(", "--include", "*.ts")
    assert result.exit_code == 1, result.output
    assert "--pattern" in result.output and "Traceback" not in result.output


def test_inventory_omission_is_reconciled(workspace, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Orders.java").write_text(
        'class Orders {\n@GetMapping("/orders/{id}")\nvoid get() {}\n'
        '@PostMapping("/orders")\nvoid create() {}\n}\n'
    )
    result = reconcile(source, workspace.path("coverage"))
    assert [item["line"] for item in result["discovery_gaps"]] == [4]
    assert len(result["mappings"]) == 2


def test_coverage_distinguishes_authored_pending_xfail_and_verified(workspace):
    case = yaml.safe_load(case_path(workspace).read_text())
    assert coverage_view(workspace.path("coverage"), [case])["pending"] == 0
    for outcome, expectation, verified in [
        ("PASS", "pending", 0),
        ("XFAIL", "confirmed", 0),
        ("ERROR", "confirmed", 0),
        ("PASS", "confirmed", 1),
    ]:
        results = {"cases": [{**case, "outcome": outcome, "expectation": expectation}]}
        assert coverage_view(workspace.path("coverage"), [case], results)["verified"] == verified


def test_incremental_anchor_and_exact_migration_exception(workspace):
    root = workspace.root
    init_git(root)
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    marker = root / "repo2test.toml"
    marker.write_text(marker.read_text().replace(SOURCE, "b" * 40))
    ws = Workspace.load(root)
    assert validate_workspace(ws, base=base) == []  # untouched rows may remain historical
    path = case_path(ws)
    original = path.read_text()
    assert migrate_case(original) == original
    path.write_text(original + "\n# manual change\n")
    assert any("changed case has stale" in e for e in validate_workspace(ws, base=base))
    (root / "uv.lock").write_text("version = 1\n")
    assert any("changed case has stale" in e for e in validate_workspace(ws, base=base))
    path.write_text(migrate_case(original))
    assert validate_workspace(ws, base=base) == []
    modified = yaml.safe_load(path.read_text())
    modified["steps"][0]["assert"]["status"] = 201
    path.write_text(yaml.safe_dump(modified))
    assert any("changed case has stale" in e for e in validate_workspace(ws, base=base))


def test_combined_candidate_catches_duplicate_coverage(workspace):
    path = case_path(workspace)
    second = yaml.safe_load(path.read_text())
    second["name"] = "another--def456"
    (path.parent / "case_another--def456.yaml").write_text(yaml.safe_dump(second))
    assert any("duplicate case coverage" in e for e in validate_workspace(workspace))


def _set_row_evidence(workspace, *kinds):
    path = workspace.path("coverage") / "orders.yaml"
    data = yaml.safe_load(path.read_text())
    data["rows"][0]["evidence"] = [
        {"file": "tests/orders.test.js", "line": 3, "kind": kind} for kind in kinds
    ]
    path.write_text(yaml.safe_dump(data))


def test_a_repository_test_confirms_an_expectation(workspace):
    _set_row_evidence(workspace, "test")
    assert validate_workspace(workspace) == []


@pytest.mark.parametrize(
    ("kinds", "test_backed"),
    [(["test"], 1), (["test", "implementation"], 1), (["test", "requirement"], 0)],
)
def test_coverage_counts_confirmed_rows_that_rest_on_tests_alone(workspace, kinds, test_backed):
    _set_row_evidence(workspace, *kinds)
    assert coverage_view(workspace.path("coverage"), [])["test_backed"] == test_backed


def test_a_pending_row_with_test_evidence_is_not_counted_as_test_backed(workspace):
    _set_row_evidence(workspace, "test")
    path = workspace.path("coverage") / "orders.yaml"
    data = yaml.safe_load(path.read_text())
    data["rows"][0]["expectation"] = "pending"
    path.write_text(yaml.safe_dump(data))
    assert coverage_view(workspace.path("coverage"), [])["test_backed"] == 0


def test_changed_row_anchor_and_contract_evidence(workspace):
    root = workspace.root
    init_git(root)
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    path = workspace.path("coverage") / "orders.yaml"
    data = yaml.safe_load(path.read_text())
    data["rows"][0]["source_commit"] = "old"
    data["rows"][0]["evidence"][0]["kind"] = "implementation"
    path.write_text(yaml.safe_dump(data))
    errors = validate_workspace(workspace, base="HEAD")
    assert any("contract evidence" in e for e in errors)
    assert any("changed coverage row has stale" in e for e in errors)


def test_readonly_source_does_not_checkout_business_changes(workspace):
    business = workspace.root.parent / "orders"
    init_git(business)
    (business / "Orders.java").write_text("version one\n")
    git(business, "add", ".")
    git(business, "commit", "-qm", "one")
    (business / "Orders.java").write_text("dirty user changes\n")
    before = git(business, "status", "--porcelain"), git(business, "symbolic-ref", "HEAD")
    checkout, commit = source_checkout(workspace, "HEAD")
    assert (checkout / "Orders.java").read_text() == "version one\n"
    assert (checkout / "Orders.java").stat().st_mode & 0o222 == 0
    assert commit == git(business, "rev-parse", "HEAD")
    assert before == (git(business, "status", "--porcelain"), git(business, "symbolic-ref", "HEAD"))
    assert (business / "Orders.java").read_text() == "dirty user changes\n"


def test_dotenv_stops_at_workspace_boundary(workspace, monkeypatch):
    (workspace.root.parent / ".env").write_text("REPO2TEST_BOUNDARY=parent\n")
    monkeypatch.delenv("REPO2TEST_BOUNDARY", raising=False)
    monkeypatch.chdir(workspace.path("tests"))
    _load_dotenv()
    import os

    assert "REPO2TEST_BOUNDARY" not in os.environ
    (workspace.root / ".env").write_text("REPO2TEST_BOUNDARY=workspace\n")
    _load_dotenv()
    assert os.environ["REPO2TEST_BOUNDARY"] == "workspace"
    monkeypatch.delenv("REPO2TEST_BOUNDARY")


def test_result_fingerprint_rejects_uncommitted_changes(workspace, monkeypatch):
    result = workspace.root / "result.json"
    result.write_text(json.dumps({"run": workspace.identity(), "cases": []}))
    monkeypatch.chdir(workspace.root)
    runner = CliRunner()
    assert runner.invoke(cli, ["coverage", "--results", str(result)]).exit_code == 0
    path = case_path(workspace)
    path.write_text(path.read_text() + "\n# manual edit\n")
    assert runner.invoke(cli, ["coverage", "--results", str(result)]).exit_code != 0


def test_entry_uses_each_workspaces_pinned_skill_from_nested_directory(workspace):
    import apitest

    locator = Path(apitest.__file__).parent / "assets/entry/repo2test/scripts/locate.py"
    for number in (1, 2):
        skill = workspace.root / f"pinned-{number}.md"
        skill.write_text(f"pinned version {number}")
        marker = workspace.root / "repo2test.toml"
        marker.write_text(f'[target]\npath = "../orders"\n[paths]\nskill = "{skill.name}"\n')
        output = subprocess.check_output(
            [sys.executable, str(locator), "--start", str(workspace.path("tests"))],
            text=True,
        )
        located = json.loads(output)
        assert located["skill"] == str(skill)
        assert located["business"] == str(workspace.root.parent / "orders")


def test_source_defaults_to_pinned_commit_after_branch_moves(workspace, monkeypatch):
    business = workspace.root.parent / "orders"
    init_git(business)
    (business / "v.txt").write_text("one")
    git(business, "add", ".")
    git(business, "commit", "-qm", "one")
    original = git(business, "rev-parse", "HEAD")
    marker = workspace.root / "repo2test.toml"
    marker.write_text(marker.read_text().replace(SOURCE, original))
    (business / "v.txt").write_text("two")
    git(business, "add", ".")
    git(business, "commit", "-qm", "two")
    monkeypatch.chdir(workspace.root)
    result = CliRunner().invoke(cli, ["workspace", "source"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["source_commit"] == original
    assert Path(output["checkout"], "v.txt").read_text() == "one"


def test_url_setup_can_leave_unknown_environment_unclassified(workspace, monkeypatch):
    monkeypatch.chdir(workspace.root)
    monkeypatch.delenv("LOCAL_ORDERS_URL", raising=False)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["workspace", "configure", "--service", "orders", "--url", "http://127.0.0.1:9"]
    )
    assert result.exit_code == 0, result.output
    profile = yaml.safe_load((workspace.path("profiles") / "local.yaml").read_text())
    assert "environment" not in profile
    assert runner.invoke(cli, ["preflight", "--id", "orders"]).exit_code == 0


def test_migrate_current_schema_preserves_manual_yaml_and_reports_no_changes(
    workspace, monkeypatch
):
    path = case_path(workspace)
    content = "# Maintainer's rationale\r\n" + path.read_text().replace("\n", "\r\n")
    path.write_bytes(content.encode())
    before = path.read_bytes()
    monkeypatch.chdir(workspace.root)
    for args in (["migrate", "--check"], ["migrate"]):
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["changed"] == []
        assert path.read_bytes() == before


def test_preflight_checks_only_the_selected_cases(workspace, monkeypatch):
    monkeypatch.chdir(workspace.root)
    (workspace.path("profiles") / "local.yaml").write_text("services: {}\n")
    payments = workspace.path("tests") / "payments"
    payments.mkdir()
    (payments / "case_broken--abc123.yaml").write_text("schema: v1\nname: broken\n")
    runner = CliRunner()
    selected = runner.invoke(cli, ["preflight", "--id", "orders"])
    assert isinstance(selected.exception, SystemExit), selected.exception
    assert [r["case"] for r in json.loads(selected.output)] == [
        "tests/orders/case_existing--abc123.yaml"
    ]
    broken = runner.invoke(cli, ["preflight", "--id", "payments"])
    assert broken.exit_code == 1 and isinstance(broken.exception, SystemExit), broken.exception
    assert "case_broken--abc123.yaml" in broken.output


def test_preflight_missing_url_can_be_configured_without_losing_cases(workspace, monkeypatch):
    monkeypatch.chdir(workspace.root)
    (workspace.path("profiles") / "local.yaml").write_text("services: {}\n")
    path = case_path(workspace)
    before = path.read_bytes()
    runner = CliRunner()
    result = runner.invoke(cli, ["preflight", "--id", "orders"])
    assert result.exit_code == 2 and "service URL" in result.output
    configured = runner.invoke(
        cli, ["workspace", "configure", "--service", "orders", "--url", "http://orders.test"]
    )
    assert configured.exit_code == 0, configured.output
    assert runner.invoke(cli, ["preflight", "--id", "orders"]).exit_code == 0
    assert path.read_bytes() == before


def test_doctor_reports_unwritable_workspace(workspace, monkeypatch):
    import tempfile

    monkeypatch.chdir(workspace.root)

    def denied(*args, **kwargs):
        raise PermissionError("workspace denied")

    monkeypatch.setattr(tempfile, "TemporaryFile", denied)
    result = CliRunner().invoke(cli, ["workspace", "doctor"])
    assert result.exit_code == 2
    assert "workspace is not writable" in result.output


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_ci_requires_ownership_for_entire_pinned_distribution(tmp_path, provider):
    from apitest.workspace_cli import _write_ci

    _write_ci(tmp_path, provider, "@runner-owner")
    owners = tmp_path / (".github/CODEOWNERS" if provider == "github" else "CODEOWNERS")
    text = owners.read_text()
    for pattern in (
        "/vendor/*.whl",
        "/uv.lock",
        "/runner.toml",
        "/.gitattributes",
        "/pyproject.toml",
        "/.claude/skills/repo2test-workspace/",
        "/tools/",
    ):
        assert f"{pattern} @runner-owner" in text
    assert "/repo2test.toml" not in text


@pytest.mark.parametrize(
    "status, verified",
    [("uncovered", 1), ("authored", 1), ("blocked", 0), ("needs-review", 0), ("gap", 0)],
)
def test_verified_coverage_uses_case_and_run_without_manual_authored_status(
    workspace, status, verified
):
    case = yaml.safe_load(case_path(workspace).read_text())
    path = workspace.path("coverage") / "orders.yaml"
    endpoint = yaml.safe_load(path.read_text())
    endpoint["rows"][0]["status"] = status
    if status in {"blocked", "needs-review", "gap"}:
        endpoint["rows"][0]["reason"] = "Recorded reason"
    path.write_text(yaml.safe_dump(endpoint))
    results = {"cases": [{**case, "outcome": "PASS", "expectation": "confirmed"}]}
    assert coverage_view(workspace.path("coverage"), [case], results)["verified"] == verified
    assert coverage_view(workspace.path("coverage"), [], results)["verified"] == 0


@pytest.mark.parametrize("status", ["gap", "blocked", "needs-review"])
def test_open_coverage_rows_must_state_a_reason(workspace, status):
    path = workspace.path("coverage") / "orders.yaml"
    endpoint = yaml.safe_load(path.read_text())
    endpoint["rows"][0]["status"] = status
    path.write_text(yaml.safe_dump(endpoint))
    assert any("needs a reason" in error for error in validate_workspace(workspace))


@pytest.mark.parametrize("name", [".SKILL.md.swp", "SKILL.md~", ".#SKILL.md", "#SKILL.md#"])
def test_editor_temp_files_are_not_pinned_asset_drift(workspace, name):
    (workspace.root / ".claude/skills/repo2test-workspace" / name).write_text("editor state")
    assert not [e for e in workspace.runner_problems() if name in e]


def test_upgrade_notes_cover_only_crossed_releases():
    import apitest
    from apitest.workspace_cli import release_notes

    notes = (Path(apitest.__file__).parent / "assets/upgrade-notes.md").read_text()
    from_020 = release_notes(notes, "0.2.0", "0.2.3")
    assert all(f"## 0.2.{n}" in from_020 for n in (1, 2, 3))
    from_022 = release_notes(notes, "0.2.2", "0.2.3")
    assert "## 0.2.3" in from_022 and "## 0.2.1" not in from_022 and "## 0.2.2" not in from_022
    assert "No compatibility notes" in release_notes(notes, "0.2.3", "0.2.3")


def test_upgrade_notes_tell_a_0_2_3_workspace_what_0_2_4_checks():
    import apitest
    from apitest.workspace_cli import release_notes

    notes = (Path(apitest.__file__).parent / "assets/upgrade-notes.md").read_text()
    crossed = release_notes(notes, "0.2.3", "0.2.4")
    assert "## 0.2.4" in crossed and "## 0.2.3" not in crossed
    assert "evidence" in crossed and "source checkout" in crossed


@pytest.mark.parametrize(
    "key",
    [
        "orders|GET|/orders/{id}|happy",  # missing anchor
        "orders|POST|/orders/{id}|happy|existing",  # method differs from the endpoint
        "payments|GET|/orders/{id}|happy|existing",  # service differs from the endpoint
        "orders|GET|/orders/{id}|unknown-category|existing",
    ],
)
def test_coverage_keys_must_match_their_endpoint_and_category(workspace, key):
    path = workspace.path("coverage") / "orders.yaml"
    endpoint = yaml.safe_load(path.read_text())
    endpoint["rows"][0]["key"] = key
    path.write_text(yaml.safe_dump(endpoint))
    assert any("invalid coverage key" in error for error in validate_workspace(workspace))


def test_endpoint_without_rows_is_rejected(workspace):
    path = workspace.path("coverage") / "orders.yaml"
    endpoint = yaml.safe_load(path.read_text())
    endpoint["rows"] = []
    path.write_text(yaml.safe_dump(endpoint))
    assert any("must retain a coverage or gap row" in e for e in validate_workspace(workspace))


def test_authored_row_without_a_case_is_rejected(workspace):
    case_path(workspace).unlink()
    assert any("authored coverage row has no case" in e for e in validate_workspace(workspace))


def test_interrupted_inventory_resumes_from_unauthored_rows(workspace):
    path = workspace.path("coverage") / "orders.yaml"
    endpoint = yaml.safe_load(path.read_text())
    endpoint["rows"].append(
        {
            "key": "orders|GET|/orders/{id}|error|missing",
            "source_commit": SOURCE,
            "evidence": [{"file": "api.yaml", "line": 12, "kind": "requirement"}],
        }
    )
    path.write_text(yaml.safe_dump(endpoint))
    assert validate_workspace(workspace) == []
    case = yaml.safe_load(case_path(workspace).read_text())
    view = coverage_view(workspace.path("coverage"), [case])
    assert view["authored"] == 1 and view["pending"] == 1
    [pending] = [r for r in view["rows"] if not r["authored"]]
    assert pending["key"] == "orders|GET|/orders/{id}|error|missing"


def test_upgrade_drops_the_retired_helper_trust_ownership_rule(tmp_path):
    from apitest.workspace_cli import _protect_runner

    owners = tmp_path / "CODEOWNERS"
    owners.write_text("/helper-trust.toml @runner-owner\n/tests/ @qa\n")
    _protect_runner(tmp_path, "@runner-owner", owners)
    text = owners.read_text()
    assert "/helper-trust.toml" not in text
    assert "/tests/ @qa" in text and "/vendor/*.whl @runner-owner" in text


def test_coverage_command_names_an_invalid_fragment_instead_of_crashing(workspace, monkeypatch):
    path = workspace.path("coverage") / "orders.yaml"
    endpoint = yaml.safe_load(path.read_text())
    endpoint["rows"][0]["status"] = "gap"
    path.write_text(yaml.safe_dump(endpoint))
    monkeypatch.chdir(workspace.root)
    result = CliRunner().invoke(cli, ["coverage"])
    assert result.exit_code == 1
    assert "orders.yaml" in result.output and "needs a reason" in result.output
    assert not isinstance(result.exception, ValueError)


@pytest.mark.parametrize(
    "reference",
    [
        "HEAD",
        "HEAD~1",
        "@",
        "HEAD@{1}",
        "@{-1}",
        "FETCH_HEAD",
        "ORIG_HEAD",
        "CHERRY_PICK_HEAD",
        "REBASE_HEAD",
        "BISECT_HEAD^",
    ],
)
def test_local_symbolic_references_are_recorded_as_commits(reference):
    from apitest.workspace_cli import _recorded_ref

    assert _recorded_ref(reference, "c" * 40) == "c" * 40
    assert _recorded_ref("v1.4.0", "c" * 40) == "v1.4.0"


def test_release_candidate_to_final_upgrade_prints_the_final_notes():
    from apitest.workspace_cli import release_notes

    notes = "Preamble\n## 0.2.3\n\n- final note\n"
    assert "final note" in release_notes(notes, "0.2.3rc1", "0.2.3")
    assert "final note" in release_notes(notes, "0.2.2", "0.2.3+gabc")
    assert "No compatibility notes" in release_notes(notes, "0.2.3", "0.2.3+gabc")
    assert "final note" in release_notes(notes, "0.2.2", "0.2.3rc1")


def test_emacs_lock_symlinks_are_not_pinned_asset_drift(workspace):
    lock = workspace.root / ".claude/skills/repo2test-workspace/.#SKILL.md"
    lock.symlink_to("user@host.1234")
    assert not [e for e in workspace.runner_problems() if ".#SKILL.md" in e]
