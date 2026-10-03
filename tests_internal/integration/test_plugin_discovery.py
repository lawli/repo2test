import textwrap

import pytest


def test_yaml_case_collected_and_passes(pytester: pytest.Pytester, respx_mock: object) -> None:
    respx_mock.post("http://example-payment/api/x").respond(200, json={"ok": True})  # type: ignore[attr-defined]

    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(textwrap.dedent("""
        services:
          example-payment: { base_url: "http://example-payment", auth_mode: jwt }
    """))

    tests = pytester.mkdir("tests")
    svc = tests / "example-payment"
    svc.mkdir()
    case = svc / "case_001.yaml"
    case.write_text(textwrap.dedent("""
        schema: v1
        mutates: false
        name: smoke
        service: example-payment
        steps:
          - name: do
            request: { method: POST, path: /api/x }
            assert: { status: 200, json: { ok: true } }
    """))

    pytester.makeini(
        "[pytest]\n"
        "apitest_profile = test\n"
        "apitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    result = pytester.runpytest("-v", "--rootdir", str(pytester.path))
    result.assert_outcomes(passed=1)


def test_invalid_case_fails_alone_and_valid_case_still_runs(
    pytester: pytest.Pytester, respx_mock: object
) -> None:
    respx_mock.post("http://example-payment/api/x").respond(200, json={"ok": True})  # type: ignore[attr-defined]

    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://example-payment' }\n"
    )
    svc = pytester.mkdir("tests") / "example-payment"
    svc.mkdir()
    (svc / "case_001.yaml").write_text(textwrap.dedent("""
        schema: v1
        mutates: false
        name: case_001
        service: example-payment
        steps:
          - name: do
            request: { method: POST, path: /api/x }
            assert: { status: 200 }
    """))
    (svc / "case_002_bad.yaml").write_text(
        "schema: v1\nname: case_002_bad\nservice: example-payment\ntags: nope\nsteps: []\n"
    )
    (svc / "case_003_unparsable.yaml").write_text("schema: v1\nname: [\n")

    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    result = pytester.runpytest("-v", "--rootdir", str(pytester.path))
    result.assert_outcomes(passed=1, failed=2)
    result.stdout.fnmatch_lines(["*case_002_bad*FAILED*", "*case_003_unparsable*FAILED*"])


def test_failure_message_includes_http_exchange(
    pytester: pytest.Pytester, respx_mock: object
) -> None:
    respx_mock.post("http://example-payment/api/x").respond(500, json={"error": "boom"})  # type: ignore[attr-defined]

    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://example-payment' }\n"
        "http: { retries: { count: 0 } }\n"
    )
    svc = pytester.mkdir("tests") / "example-payment"
    svc.mkdir()
    (svc / "case_001.yaml").write_text(textwrap.dedent("""
        schema: v1
        mutates: false
        name: case_001
        service: example-payment
        steps:
          - name: do
            request: { method: POST, path: /api/x }
            assert: { status: 200 }
    """))
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    result = pytester.runpytest("--rootdir", str(pytester.path))
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([
        "*status: expected 200, got 500*",
        "*POST http://example-payment/api/x -> 500*",
        '*{"error":*boom*',
    ])


def test_parallel_safe_true_is_not_pinned_to_service_group(pytester: pytest.Pytester) -> None:
    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://example-payment' }\n"
    )
    svc = pytester.mkdir("tests") / "example-payment"
    svc.mkdir()
    for name, flag in (("case_001_pinned", ""), ("case_002_free", "parallel_safe: true\n")):
        (svc / f"{name}.yaml").write_text(
            f"schema: v1\nname: {name}\nservice: example-payment\n{flag}"
            "steps:\n  - name: a\n    request: { method: GET, path: /x }\n"
        )
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    items, _ = pytester.inline_genitems("tests")
    groups = {i.name: i.get_closest_marker("xdist_group") for i in items}
    assert groups["case_001_pinned"].kwargs["name"] == "apitest-serial"
    assert groups["case_002_free"] is None


_GET_CASE = textwrap.dedent("""
    schema: v1
    name: smoke
    service: example-payment
    steps:
      - name: read
        request: { method: GET, path: /x }
        assert: { status: 200 }
""")


def _env_project(pytester: pytest.Pytester, *, workspace: bool) -> None:
    if workspace:
        (pytester.path / "repo2test.toml").write_text("")
    (pytester.path / ".env").write_text("PLUGIN_ENV_URL=http://from-dotenv\n")
    (pytester.mkdir("profiles") / "test.yaml").write_text(
        'services:\n  example-payment: { base_url: "${PLUGIN_ENV_URL}" }\n'
    )
    svc = pytester.mkdir("tests") / "example-payment"
    svc.mkdir()
    (svc / "case_001.yaml").write_text(_GET_CASE)
    pytester.makeini("[pytest]\napitest_profile = test\n")


@pytest.fixture
def plugin_env_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # The in-process run sets PLUGIN_ENV_URL in this process. Setting then deleting it makes
    # monkeypatch remove it again afterwards, so it cannot leak into later tests.
    monkeypatch.setenv("PLUGIN_ENV_URL", "unset")
    monkeypatch.delenv("PLUGIN_ENV_URL")


def test_direct_pytest_in_workspace_loads_dotenv(
    pytester: pytest.Pytester, respx_mock: object, plugin_env_url_unset: None
) -> None:
    respx_mock.get("http://from-dotenv/x").respond(200)  # type: ignore[attr-defined]
    _env_project(pytester, workspace=True)
    result = pytester.runpytest("-v", "--rootdir", str(pytester.path))
    result.assert_outcomes(passed=1)


def test_shell_env_wins_over_workspace_dotenv(
    pytester: pytest.Pytester, respx_mock: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLUGIN_ENV_URL", "http://from-shell")
    respx_mock.get("http://from-shell/x").respond(200)  # type: ignore[attr-defined]
    _env_project(pytester, workspace=True)
    result = pytester.runpytest("-v", "--rootdir", str(pytester.path))
    result.assert_outcomes(passed=1)


def test_dotenv_outside_a_workspace_is_not_loaded(
    pytester: pytest.Pytester, plugin_env_url_unset: None
) -> None:
    _env_project(pytester, workspace=False)
    result = pytester.runpytest("-v", "--rootdir", str(pytester.path))
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*required env var not set: PLUGIN_ENV_URL*"])
