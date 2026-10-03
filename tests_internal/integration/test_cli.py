import os
import subprocess
from pathlib import Path

import pytest


def _scaffold(tmp_path: Path) -> Path:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles/test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://x', auth_mode: jwt }\n"
    )
    (tmp_path / "tests/example-payment").mkdir(parents=True)
    (tmp_path / "tests/example-payment/case_001.yaml").write_text(
        "schema: v1\nname: case_001\nservice: example-payment\n"
        "steps:\n  - name: a\n    request: { method: GET, path: /x }\n    assert: { status: 200 }\n"
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\n"
        "apitest_profile = test\n"
        "apitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    return tmp_path


def test_cli_list(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    p = subprocess.run(
        ["apitest", "list", "--service", "example-payment"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "case_001" in p.stdout


def test_cli_validate_passes(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    p = subprocess.run(
        ["apitest", "validate", "tests"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr


def _write_case_with_service(root: Path, service: str) -> None:
    (root / "tests/example-payment/case_001.yaml").write_text(
        f"schema: v1\nname: case_001\nservice: {service}\n"
        "steps:\n  - name: a\n    request: { method: GET, path: /x }\n    assert: { status: 200 }\n"
    )


def test_cli_validate_profile_flags_unknown_service(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_case_with_service(root, "nonesuch")  # not declared in profile test.yaml
    p = subprocess.run(
        ["apitest", "validate", "tests", "--profile", "test"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 2, (p.stdout, p.stderr)
    assert "nonesuch" in (p.stdout + p.stderr)


def test_cli_validate_without_profile_ignores_service_names(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_case_with_service(root, "nonesuch")  # accepted: no profile to check against
    p = subprocess.run(
        ["apitest", "validate", "tests"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)


def _write_tagged_cases(root: Path) -> None:
    (root / "tests/example-payment/case_001.yaml").write_text(
        "schema: v1\nname: case_001\nservice: example-payment\n"
        "tags: [p0, smoke]\n"
        "steps:\n  - name: a\n    request: { method: GET, path: /x }\n    assert: { status: 200 }\n"
    )
    (root / "tests/example-payment/case_002.yaml").write_text(
        "schema: v1\nname: case_002\nservice: example-payment\n"
        "tags: [p0]\n"
        "steps:\n  - name: a\n    request: { method: GET, path: /x }\n    assert: { status: 200 }\n"
    )


def test_cli_run_tag_filters_by_case_tags(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_tagged_cases(root)
    p = subprocess.run(
        ["apitest", "run", "--id", "example-payment", "--tag", "smoke", "--dry-run"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "case_001" in p.stdout
    assert "case_002" not in p.stdout


def test_cli_run_tags_are_and_combined(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_tagged_cases(root)
    p = subprocess.run(
        ["apitest", "run", "--id", "example-payment",
         "--tag", "p0", "--tag", "smoke", "--dry-run"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "case_001" in p.stdout
    assert "case_002" not in p.stdout


def test_cli_run_no_tag_match_errors_instead_of_running_everything(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_tagged_cases(root)
    p = subprocess.run(
        ["apitest", "run", "--id", "example-payment", "--tag", "nonesuch", "--dry-run"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 2, (p.stdout, p.stderr)
    assert "no cases match" in (p.stdout + p.stderr)


def test_cli_run_tag_surfaces_schema_error_for_scalar_tags(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_tagged_cases(root)
    (root / "tests/example-payment/case_bad.yaml").write_text(
        "schema: v1\nname: case_bad\nservice: example-payment\n"
        "tags: smoke\n"  # scalar, schema-invalid — must not be silently dropped
        "steps:\n  - name: a\n    request: { method: GET, path: /x }\n    assert: { status: 200 }\n"
    )
    p = subprocess.run(
        ["apitest", "run", "--id", "example-payment", "--tag", "smoke", "--dry-run"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode != 0, (p.stdout, p.stderr)
    assert "case_bad" in (p.stdout + p.stderr)


def test_cli_run_case_without_suite_selects_single_case(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    (root / "tests/example-payment/scen").mkdir()
    for name in ("case_010", "case_020"):
        (root / f"tests/example-payment/scen/{name}.yaml").write_text(
            f"schema: v1\nname: {name}\nservice: example-payment\n"
            "steps:\n  - name: a\n    request: { method: GET, path: /x }\n"
            "    assert: { status: 200 }\n"
        )
    p = subprocess.run(
        ["apitest", "run", "--service", "example-payment", "--scenario", "scen",
         "--case", "case_010", "--dry-run"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "case_010" in p.stdout
    assert "case_020" not in p.stdout


def test_cli_run_case_requires_scenario(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    p = subprocess.run(
        ["apitest", "run", "--service", "example-payment",
         "--case", "case_001", "--dry-run"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 2, (p.stdout, p.stderr)
    assert "--scenario" in (p.stdout + p.stderr)


def test_cli_list_accepts_id_selector(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    p = subprocess.run(
        ["apitest", "list", "--id", "example-payment"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "case_001" in p.stdout


def test_cli_profile_show_redacts(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    (root / "profiles/test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://x', auth_mode: jwt }\n"
        "databases:\n  example-payment:\n    dsn: 'mysql://${DB_USER}:${DB_PASS}@h/d'\n"
    )
    env = os.environ | {"DB_USER": "u", "DB_PASS": "hunter2"}
    p = subprocess.run(
        ["apitest", "profile", "show", "test"],
        cwd=root, capture_output=True, text=True, env=env,
    )
    assert p.returncode == 0, p.stderr
    assert "hunter2" not in p.stdout
    assert "***" in p.stdout


def _write_redis_seed(root: Path) -> Path:
    seed = root / "tests/example-payment/fixtures/redis/seed.yaml"
    seed.parent.mkdir(parents=True)
    seed.write_text("set:\n  - { key: 'session:1', value: abc, ttl: 60 }\n")
    return seed


def test_cli_list_ignores_fixture_yaml(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_redis_seed(root)
    p = subprocess.run(
        ["apitest", "list", "--id", "example-payment"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "case_001" in p.stdout
    assert "seed" not in p.stdout


def test_cli_validate_still_secret_scans_fixture_yaml(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    seed = _write_redis_seed(root)
    seed.write_text("set:\n  - { key: dsn, value: 'mysql://u:hunter2secret@h/d' }\n")
    p = subprocess.run(
        ["apitest", "validate", "tests"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 2, (p.stdout, p.stderr)
    assert "seed.yaml" in (p.stdout + p.stderr)


@pytest.mark.parametrize("args", [["list", "--service", "nope"], ["run", "--id", "nope"]])
def test_cli_unmatched_selector_prints_one_line(tmp_path: Path, args: list[str]) -> None:
    root = _scaffold(tmp_path)
    p = subprocess.run(["apitest", *args], cwd=root, capture_output=True, text=True)
    assert p.returncode == 1, p.stderr
    assert "Traceback" not in p.stderr
    assert p.stderr.startswith("Error: no case matches 'nope'")


def test_cli_validate_reports_literal_credentials_in_every_profile(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    (root / "profiles/staging.yaml").write_text(
        "services:\n  example-payment:\n    base_url: 'http://x'\n"
        "    headers: { X-Api-Key: 'k-hunter2' }\n"
    )
    p = subprocess.run(
        ["apitest", "validate", "tests"],
        cwd=root, capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 2, out
    assert "staging.yaml: services.example-payment.headers.X-Api-Key holds a literal value" in out
    assert "${STAGING_EXAMPLE_PAYMENT_X_API_KEY}" in out
    assert "hunter2" not in out


def test_cli_validate_selected_profile_reports_each_literal_once(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    (root / "profiles/test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://x' }\n"
        "rabbitmq:\n  default: { url: 'amqp://guest:hunter2@h/' }\n"
    )
    p = subprocess.run(
        ["apitest", "validate", "tests", "--profile", "test"],
        cwd=root, capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 2, out
    assert out.count("rabbitmq.default.url has a literal URL password") == 1
    assert "Traceback" not in out
    assert "hunter2" not in out


def test_cli_validate_reports_a_symlinked_profile_once(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    (root / "shared").mkdir()
    (root / "shared/staging.yaml").write_text(
        "rabbitmq:\n  default: { url: 'amqp://guest:hunter2@h/' }\n"
    )
    (root / "profiles/staging.yaml").symlink_to(root / "shared/staging.yaml")
    p = subprocess.run(
        ["apitest", "validate", "tests", "--profile", "staging"],
        cwd=root, capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 2, out
    assert out.count("rabbitmq.default.url has a literal URL password") == 1


@pytest.mark.parametrize("extra", [[], ["--profile", "staging"]])
def test_cli_validate_never_prints_values_from_invalid_profile_yaml(
    tmp_path: Path, extra: list[str]
) -> None:
    root = _scaffold(tmp_path)
    (root / "profiles/staging.yaml").write_text(
        "services:\n  example-payment:\n    headers: { Authorization: \"Bearer hunter2 }\n"
    )
    p = subprocess.run(
        ["apitest", "validate", "tests", *extra],
        cwd=root, capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 2, out
    assert "staging.yaml" in out
    assert "Traceback" not in out
    assert "hunter2" not in out


def test_cli_invalid_profile_yaml_prints_one_line_without_the_source(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    (root / "profiles/test.yaml").write_text(
        'services:\n  example-payment:\n    headers: { Authorization: "Bearer hunter2 }\n'
    )
    p = subprocess.run(
        ["apitest", "profile", "show", "test"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 1, p.stderr
    assert p.stderr.startswith("Error: invalid profile YAML:")
    assert "Traceback" not in p.stderr
    assert "hunter2" not in p.stdout + p.stderr


def test_cli_profile_errors_print_one_line_not_a_traceback(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    (root / "profiles/test.yaml").write_text(
        "rabbitmq:\n  default: { url: 'amqp://guest:hunter2@h/' }\n"
    )
    p = subprocess.run(
        ["apitest", "profile", "show", "test"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 1, p.stderr
    assert "Traceback" not in p.stderr
    assert p.stderr.startswith("Error: literal credentials in committed profiles:")
    assert "${TEST_MQ_PASS}" in p.stderr
    assert "hunter2" not in p.stdout + p.stderr


def _write_bad_case(root: Path) -> None:
    (root / "tests/example-payment/case_002_bad.yaml").write_text(
        "schema: v1\nname: case_002_bad\nservice: example-payment\n"
        "tags: nope\n"  # scalar tags: schema-invalid
        "steps:\n  - name: a\n    request: { method: GET, path: /x }\n"
    )


def test_cli_run_invalid_case_does_not_block_other_cases(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_bad_case(root)
    p = subprocess.run(
        ["apitest", "run", "--id", "example-payment", "--report", "junit",
         "--report-dir", str(root / "reports")],
        cwd=root, capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 1, out  # test failures, not a usage/collection abort (4)
    assert "collected 2 items" in out
    assert "case_002_bad" in out and "validation error" in out
    # case_001 ran (and failed: http://x is unreachable) rather than being skipped
    assert "case_001" in out and "case_001.yaml" in out


def test_cli_dry_run_reports_invalid_case_without_running(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    _write_bad_case(root)
    p = subprocess.run(
        ["apitest", "run", "--id", "example-payment", "--dry-run"],
        cwd=root, capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    assert p.returncode == 2, out
    assert "case_002_bad.yaml" in out and "validation error" in out


def test_cli_validate_lints_sql_fixtures(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    sql = root / "tests/example-payment/fixtures/db/cleanup.sql"
    sql.parent.mkdir(parents=True)
    sql.write_text("DELETE FROM t_order;\n")
    p = subprocess.run(
        ["apitest", "validate", "tests"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 2, (p.stdout, p.stderr)
    assert "cleanup.sql" in p.stderr and "blanket-delete" in p.stderr


def test_cli_run_writes_summary_json(tmp_path: Path) -> None:
    import json

    root = _scaffold(tmp_path)
    p = subprocess.run(
        ["apitest", "run", "--id", "example-payment", "--report", "junit",
         "--report-dir", str(root / "reports")],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 1, p.stdout + p.stderr  # http://x is unreachable -> case errors
    summary = json.loads((root / "reports/summary.json").read_text())
    assert summary["totals"]["error"] == 1
    assert summary["cases"][0]["name"] == "case_001"


def test_cli_connections_show_reports_per_case_model(tmp_path: Path) -> None:
    import json

    root = _scaffold(tmp_path)
    (root / "profiles/test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://x' }\n"
        "databases:\n  example-payment: { dsn: 'mysql://u@h/d' }\n"
        "redis:\n  default: { url: 'redis://h/0' }\n"
    )
    p = subprocess.run(
        ["apitest", "connections", "show", "--profile", "test", "--parallel", "3"],
        cwd=root, capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["workers"] == 3
    assert out["per_case"] == {"http": 1, "mysql": 1, "redis": 1, "rabbitmq": 0}
    assert out["max_concurrent"] == 9


def test_cli_validate_literal_id_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    sql = root / "tests/example-payment/_shared/fixtures/db/seed.sql"
    sql.parent.mkdir(parents=True)
    sql.write_text(
        "INSERT INTO t_order (id, amount, created_at) "
        "VALUES (${ctx.case.order_id}, 100, '2024-01-01');\n"
    )
    p = subprocess.run(["apitest", "validate", "tests"], cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "warning" in p.stderr and "literal-id" in p.stderr and "seed.sql" in p.stderr


def test_cli_validate_lints_sql_under_shared(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    sql = root / "tests/example-payment/_shared/fixtures/db/cleanup.sql"
    sql.parent.mkdir(parents=True)
    sql.write_text("DELETE FROM t_order;\n")
    p = subprocess.run(["apitest", "validate", "tests"], cwd=root, capture_output=True, text=True)
    assert p.returncode == 2 and "blanket-delete" in p.stderr
