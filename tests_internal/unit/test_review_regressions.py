import pytest

from apitest.preflight import prepare_profile
from apitest.profile import Profile
from apitest.runner.lifecycle import CaseOutcome, run_case
from apitest.schema.case_v1 import Case


def model(**fields):
    return Case.model_validate(
        {
            "schema": "v1",
            "name": "probe",
            "service": "shop",
            "steps": [
                {
                    "name": "read",
                    "request": {"method": "GET", "path": "/read"},
                    "assert": {"status": 200},
                }
            ],
            **fields,
        }
    )


def production():
    return Profile(
        name="prod", environment="production", services={"shop": {"base_url": "http://shop"}}
    )


@pytest.fixture
def helper_suite(tmp_path):
    """A readonly helper that records whether it was ever imported."""
    suite = tmp_path / "tests/shop/suite"
    helpers = suite / "_helpers"
    helpers.mkdir(parents=True)
    sentinel = tmp_path / "imported"
    (helpers / "checks.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
        "def check(ctx): assert ctx is not None\n"
    )
    return suite, sentinel


def run_with_helpers(case, profile, suite):
    from apitest.escape import HelperResolver

    return run_case(
        case,
        profile=profile,
        scope_dir=suite,
        helper_resolver=HelperResolver(suite.parent.parent),
        case_yaml_path=suite / "case_probe.yaml",
    )


@pytest.mark.parametrize("phase", ["setup", "verify"])
def test_readonly_helper_case_runs_on_nonproduction_without_write_prerequisites(
    helper_suite, phase, respx_mock
):
    suite, sentinel = helper_suite
    respx_mock.get("http://shop/read").respond(200)
    profile = production()
    profile.environment = "non-production"
    case = model(mutates=False, **{phase: {"python": [{"call": "helpers.checks.check"}]}})
    result = run_with_helpers(case, profile, suite)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    assert sentinel.exists()


@pytest.mark.parametrize("phase", ["setup", "verify"])
@pytest.mark.parametrize("environment", ["production", None])
def test_helper_cases_never_run_outside_nonproduction(helper_suite, phase, environment, respx_mock):
    suite, sentinel = helper_suite
    profile = production()
    profile.environment = environment
    case = model(mutates=False, **{phase: {"python": [{"call": "helpers.checks.check"}]}})
    result = run_with_helpers(case, profile, suite)
    assert result.outcome == CaseOutcome.BLOCKED
    assert "non-production" in result.error_message
    assert "helpers.checks.check" in result.error_message
    assert "data.isolated" not in result.error_message
    assert not sentinel.exists() and not respx_mock.calls


def test_helper_without_readonly_flag_needs_write_prerequisites(helper_suite, respx_mock):
    suite, sentinel = helper_suite
    profile = production()
    profile.environment = "non-production"
    case = model(verify={"python": [{"call": "helpers.checks.check"}]})
    result = run_with_helpers(case, profile, suite)
    assert result.outcome == CaseOutcome.BLOCKED
    assert "data.isolated" in result.error_message
    assert "helpers.checks.check" in result.error_message
    assert not sentinel.exists() and not respx_mock.calls


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        (
            {"setup": {"steps": [{"name": "login", "request": {"method": "POST", "path": "/l"}}]}},
            "setup step 'login'",
        ),
        ({"setup": {"db": ["seed.sql"]}}, "setup.db"),
        ({"setup": {"redis": ["seed.yaml"]}}, "setup.redis"),
        ({"data": {"resources": ["order created by POST /orders"]}}, "data.resources"),
        (
            {"steps": [{"name": "cancel", "request": {"method": "PATCH", "path": "/o/1"}}]},
            "step 'cancel' uses PATCH",
        ),
    ],
)
def test_readonly_flag_rejects_setup_writes_and_created_resources(fields, named):
    with pytest.raises(ValueError, match="mutates: false cannot be combined") as exc:
        model(mutates=False, **fields)
    assert named in str(exc.value)


def test_write_blocker_names_the_offending_setup_step(respx_mock):
    case = model(
        setup={"steps": [{"name": "login", "request": {"method": "POST", "path": "/login"}}]}
    )
    profile = production()
    profile.environment = "non-production"
    result = run_case(case, profile=profile, scope_dir=None)
    assert result.outcome == CaseOutcome.BLOCKED
    assert "setup step 'login'" in result.error_message
    assert not respx_mock.calls


def test_login_as_execute_step_keeps_a_readonly_case_runnable(respx_mock):
    respx_mock.post("http://shop/login").respond(200, json={"token": "t0k"})
    respx_mock.get("http://shop/read").respond(200)
    case = model(
        mutates=False,
        steps=[
            {
                "name": "login",
                "request": {"method": "POST", "path": "/login"},
                "extract": {"token": "$.token"},
            },
            {
                "name": "read",
                "request": {
                    "method": "GET",
                    "path": "/read",
                    "headers": {"Authorization": "Bearer ${ctx.token}"},
                },
                "assert": {"status": 200},
            },
        ],
    )
    profile = production()
    profile.environment = "non-production"
    assert run_case(case, profile=profile, scope_dir=None).outcome == CaseOutcome.PASS


@pytest.mark.parametrize(
    "fields",
    [
        {
            "teardown": {
                "steps": [{"name": "cancel", "request": {"method": "POST", "path": "/cancel"}}]
            }
        },
        {
            "teardown": {
                "steps": [{"name": "read-again", "request": {"method": "GET", "path": "/read"}}]
            }
        },
        {"data": {"cleanup": "Cancel the created order"}},
    ],
)
def test_readonly_flag_with_cleanup_is_invalid_before_runtime(fields):
    with pytest.raises(ValueError, match="mutates: false cannot be combined"):
        model(mutates=False, **fields)


def test_plain_readonly_post_still_executes_on_production(respx_mock):
    respx_mock.post("http://shop/search").respond(200)
    case = model(
        mutates=False,
        steps=[
            {
                "name": "search",
                "request": {"method": "POST", "path": "/search"},
                "assert": {"status": 200},
            }
        ],
    )
    assert run_case(case, profile=production(), scope_dir=None).outcome == CaseOutcome.PASS


@pytest.mark.parametrize("payload", [b"\x00\xff{{ incomplete\x80", b"name,value\n{{ user },1\n"])
def test_uploads_and_metadata_are_not_parsed_as_templates(tmp_path, respx_mock, payload):
    (tmp_path / "input.bin").write_bytes(payload)
    request = respx_mock.post("http://shop/inspect").respond(200)
    case = model(
        description="Literal {{ is documentation",
        tags=["${ctx.profile.test_data.unused}"],
        mutates=False,
        steps=[
            {
                "name": "inspect",
                "request": {"method": "POST", "path": "/inspect", "files": {"file": "input.bin"}},
                "assert": {"status": 200},
            }
        ],
    )
    result = run_case(case, profile=production(), scope_dir=tmp_path)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    assert payload in request.calls[0].request.content


def test_body_vars_remain_literal_but_rendered_template_dependencies_are_required(
    tmp_path, respx_mock
):
    (tmp_path / "body.j2").write_text(
        '{"label": {{ label | tojson }}, "account": {{ ctx.profile.test_data["account"] }} }'
    )
    case = model(
        mutates=False,
        steps=[
            {
                "name": "search",
                "request": {
                    "method": "POST",
                    "path": "/search",
                    "body_template": "body.j2",
                    "body_vars": {"label": "{{ literal"},
                },
                "assert": {"status": 200},
            }
        ],
    )
    p = production()
    _, missing = prepare_profile(case, p, scope_dir=tmp_path)
    assert any("test_data.account" in item for item in missing)
    p.test_data = {"account": 12}
    respx_mock.post("http://shop/search", json={"label": "{{ literal", "account": 12}).respond(200)
    assert run_case(case, profile=p, scope_dir=tmp_path).outcome == CaseOutcome.PASS


def test_bad_actual_template_blocks_before_requests(respx_mock):
    case = model(headers={"X-Rendered": "{{ incomplete"})
    result = run_case(case, profile=production(), scope_dir=None)
    assert result.outcome == CaseOutcome.BLOCKED
    assert "invalid dependency template" in result.error_message
    assert not respx_mock.calls


def test_unused_headers_do_not_create_dependencies():
    case = model(
        service_headers={"unused-service": {"X": "{{ broken"}},
        headers={"X": "{{ replaced"},
        steps=[
            {
                "name": "read",
                "request": {"method": "GET", "path": "/read", "headers": {"x": "literal"}},
            }
        ],
    )
    _, missing = prepare_profile(case, production())
    assert missing == []


def _order_case(**fields):
    return Case.model_validate(
        {
            "schema": "v1",
            "name": "create-order",
            "service": "shop",
            "data": {"isolated": True, "cleanup": "Delete the created order"},
            "setup": {
                "steps": [
                    {
                        "name": "login",
                        "request": {"method": "POST", "path": "/login"},
                        "assert": {"status": 200},
                        "extract": {"token": "$.token"},
                    },
                    {
                        "name": "create",
                        "request": {"method": "POST", "path": "/orders"},
                        "extract": {"order_id": "$.id"},
                    },
                ]
            },
            "steps": [
                {
                    "name": "read",
                    "request": {"method": "GET", "path": "/orders/${ctx.order_id}"},
                    "assert": {"status": 200},
                }
            ],
            "teardown": {
                "steps": [
                    {
                        "name": "delete",
                        "request": {"method": "DELETE", "path": "/orders/${ctx.order_id}"},
                    }
                ]
            },
            **fields,
        }
    )


def _staging():
    p = production()
    p.environment = "non-production"
    return p


def test_cleanup_is_skipped_not_failed_when_the_resource_was_never_requested(respx_mock):
    respx_mock.post("http://shop/login").respond(500)
    create = respx_mock.post("http://shop/orders").respond(201, json={"id": 7})
    result = run_case(_order_case(), profile=_staging(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.teardown_errors == [] and result.residual_data == {}
    assert "cleanup step 'delete' skipped" in result.error_message
    assert "cleanup failed" not in result.error_message
    assert not create.called


def test_cleanup_is_skipped_when_the_create_request_never_connected(respx_mock):
    import httpx

    respx_mock.post("http://shop/login").respond(200, json={"token": "t"})
    respx_mock.post("http://shop/orders").mock(side_effect=httpx.ConnectError("refused"))
    result = run_case(_order_case(), profile=_staging(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.teardown_errors == [] and result.residual_data == {}


def test_cleanup_still_fails_when_a_created_resource_lost_its_identifier(respx_mock):
    respx_mock.post("http://shop/login").respond(200, json={"token": "t"})
    respx_mock.post("http://shop/orders").respond(201, json={"unexpected": True})
    result = run_case(_order_case(), profile=_staging(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.teardown_errors and "cleanup failed" in result.error_message
    assert result.residual_data


def test_cleanup_failure_names_the_step_that_did_not_return_the_identifier(respx_mock):
    respx_mock.post("http://shop/login").respond(200, json={"token": "secret-token"})
    respx_mock.post("http://shop/orders").respond(500, json={"error": "boom"})
    result = run_case(_order_case(), profile=_staging(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    [error] = result.teardown_errors
    assert "ctx.order_id" in error and "step 'create' answered HTTP 500" in error
    assert "may still exist" in error
    assert "_Box" not in error


def _delete_accepting(*statuses):
    return {
        "steps": [
            {
                "name": "delete",
                "request": {"method": "DELETE", "path": "/orders/${ctx.order_id}"},
                "assert": {"status": list(statuses)},
            }
        ]
    }


def _created_order(respx_mock):
    respx_mock.post("http://shop/login").respond(200, json={"token": "secret-token"})
    respx_mock.post("http://shop/orders").respond(201, json={"id": 7})
    respx_mock.get("http://shop/orders/7").respond(200)


def test_teardown_step_accepts_any_listed_status(respx_mock):
    _created_order(respx_mock)
    respx_mock.delete("http://shop/orders/7").respond(404)
    case = _order_case(teardown=_delete_accepting(204, 404))
    result = run_case(case, profile=_staging(), scope_dir=None)
    assert result.outcome == CaseOutcome.PASS and result.teardown_errors == []


def test_teardown_step_fails_on_a_status_outside_its_list(respx_mock):
    _created_order(respx_mock)
    respx_mock.delete("http://shop/orders/7").respond(500)
    case = _order_case(teardown=_delete_accepting(204, 404))
    result = run_case(case, profile=_staging(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert "status: expected one of [204, 404], got 500" in result.teardown_errors[0]


@pytest.mark.parametrize("phase", ["setup", "steps"])
def test_status_list_is_rejected_outside_teardown(phase):
    step = {
        "name": "probe-list",
        "request": {"method": "GET", "path": "/x"},
        "assert": {"status": [200, 204]},
    }
    fields = {"setup": {"steps": [step]}} if phase == "setup" else {"steps": [step]}
    with pytest.raises(ValueError, match="only in teardown"):
        model(**fields)


def test_empty_status_list_is_rejected():
    with pytest.raises(ValueError):
        _order_case(teardown=_delete_accepting())


def test_known_defect_marker_matches_a_missing_extract(respx_mock):
    respx_mock.get("http://shop/read").respond(200, json={})
    case = model(
        steps=[
            {
                "name": "read",
                "request": {"method": "GET", "path": "/read"},
                "assert": {"status": 200},
                "extract": {"order_id": "$.id"},
            }
        ],
        known_defects=[{"ref": "#1", "step": "read", "at": "extract.order_id"}],
    )
    result = run_case(case, profile=production(), scope_dir=None)
    assert result.outcome == CaseOutcome.XFAIL
    assert result.failed_at == "extract.order_id"


def test_known_defect_marker_rejects_an_extract_the_step_does_not_declare():
    with pytest.raises(ValueError, match="unknown assertion"):
        model(known_defects=[{"ref": "#1", "step": "read", "at": "extract.order_id"}])


def _dropped(respx_mock, error):
    import httpx

    respx_mock.get("http://shop/read").mock(side_effect=getattr(httpx, error)("dropped"))


@pytest.mark.parametrize("error", ["RemoteProtocolError", "ReadError"])
def test_response_marker_matches_a_connection_dropped_without_a_response(respx_mock, error):
    _dropped(respx_mock, error)
    case = model(known_defects=[{"ref": "#1", "step": "read", "at": "response"}])
    result = run_case(case, profile=production(), scope_dir=None)
    assert result.outcome == CaseOutcome.XFAIL
    assert result.failed_at == "response"
    assert result.business_outcome == "ERROR"


@pytest.mark.parametrize(
    "markers",
    [[], [{"ref": "#1", "step": "read"}], [{"ref": "#1", "step": "read", "at": "status"}]],
)
def test_dropped_connection_is_an_error_without_a_response_marker(respx_mock, markers):
    _dropped(respx_mock, "RemoteProtocolError")
    result = run_case(model(known_defects=markers), profile=production(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert "EXECUTE error" in result.error_message


@pytest.mark.parametrize("error", ["ConnectError", "ConnectTimeout", "ReadTimeout"])
def test_response_marker_does_not_match_an_unreachable_or_slow_service(respx_mock, error):
    _dropped(respx_mock, error)
    case = model(known_defects=[{"ref": "#1", "step": "read", "at": "response"}])
    result = run_case(case, profile=production(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.failed_at is None


def test_response_marker_on_another_step_does_not_match(respx_mock):
    import httpx

    def get(name):
        request = {"method": "GET", "path": f"/{name}"}
        return {"name": name, "request": request, "assert": {"status": 200}}

    respx_mock.get("http://shop/read").respond(200)
    respx_mock.get("http://shop/again").mock(side_effect=httpx.RemoteProtocolError("dropped"))
    case = model(
        steps=[get("read"), get("again")],
        known_defects=[{"ref": "#1", "step": "read", "at": "response"}],
    )
    result = run_case(case, profile=production(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR


def test_skipped_cleanup_names_created_values_it_also_references(respx_mock):
    import httpx

    respx_mock.post("http://shop/login").respond(200, json={"token": "secret-token"})
    respx_mock.post("http://shop/orders").respond(201, json={"id": 7})
    respx_mock.post("http://shop/orders/7/items").mock(side_effect=httpx.ConnectError("refused"))
    case = _order_case()
    case.setup.steps.append(
        case.setup.steps[1].model_copy(
            update={
                "name": "add-item",
                "request": case.setup.steps[1].request.model_copy(
                    update={"path": "/orders/${ctx.order_id}/items"}
                ),
                "extract": {"item_id": "$.id"},
            }
        )
    )
    case.teardown.steps[0].request.path = "/orders/${ctx.order_id}/items/${ctx.item_id}"
    result = run_case(case, profile=_staging(), scope_dir=None)
    assert result.teardown_errors == []
    assert (
        "cleanup step 'delete' skipped: item_id never created; "
        "it also references order_id: check for manual cleanup"
    ) in result.error_message


def test_setup_failure_records_the_failed_setup_step(respx_mock):
    respx_mock.post("http://shop/login").respond(500)
    result = run_case(_order_case(), profile=_staging(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.failed_step == "login"


def test_double_at_escapes_a_literal_value_in_validation_and_at_runtime(respx_mock):
    from apitest.validation import matcher_errors

    assert matcher_errors({"owner": "@@alice"}) == []
    [error] = matcher_errors({"owner": "@alice"})
    assert "'@@alice'" in error
    respx_mock.get("http://shop/read").respond(200, json={"owner": "@alice"})
    case = model(
        steps=[
            {
                "name": "read",
                "request": {"method": "GET", "path": "/read"},
                "assert": {"status": 200, "json": {"owner": "@@alice"}},
            }
        ]
    )
    assert run_case(case, profile=production(), scope_dir=None).outcome == CaseOutcome.PASS


def test_relative_cli_paths_mean_the_callers_directory(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from apitest.cli import cli

    root = tmp_path / "ws"
    caller = root / "tests"
    (caller / "shop").mkdir(parents=True)
    (caller / "shop" / "case_ok.yaml").write_text(
        "schema: v1\nname: ok\nservice: shop\nsteps:\n"
        "  - name: read\n    request: {method: GET, path: /read}\n    assert: {status: 200}\n"
    )
    monkeypatch.chdir(caller)
    result = CliRunner().invoke(cli, ["--workspace", str(root), "validate", "shop"])
    assert result.exit_code == 0, result.output
    assert "validated 1 yaml" in result.output


def test_entry_update_removes_files_dropped_from_the_new_version(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    import apitest

    installer = Path(apitest.__file__).parent / "assets/install_entry.py"
    skills = tmp_path / "skills"
    command = [sys.executable, str(installer), "--host", "codex", "--directory", str(skills)]
    subprocess.run(command, check=True, capture_output=True)
    stale = skills / "repo2test/obsolete.md"
    stale.write_text("removed upstream")
    subprocess.run([*command, "--update"], check=True, capture_output=True)
    assert not stale.exists()
    assert (skills / "repo2test/SKILL.md").is_file()
    assert not (skills / ".repo2test-update").exists()


def test_cleanup_is_kept_when_a_delivered_request_was_retried_into_a_connect_error(respx_mock):
    import httpx

    respx_mock.post("http://shop/login").respond(200, json={"token": "t"})
    respx_mock.put("http://shop/orders").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.ConnectError("refused")]
    )
    case = _order_case()
    case.setup.steps[1].request.method = "PUT"
    profile = _staging()
    profile.http = {"retries": {"count": 1, "backoff_s": 0}}
    result = run_case(case, profile=profile, scope_dir=None)
    assert result.teardown_errors and "cleanup failed" in result.error_message
    assert "skipped" not in result.error_message


def _install_entry(skills, *extra):
    import runpy
    import sys
    from pathlib import Path

    import apitest

    installer = Path(apitest.__file__).parent / "assets/install_entry.py"
    argv = ["install_entry.py", "--host", "claude", "--directory", str(skills), *extra]
    old_argv, sys.argv = sys.argv, argv
    try:
        runpy.run_path(str(installer), run_name="__main__")
    finally:
        sys.argv = old_argv


def test_entry_update_names_the_saved_entry_when_it_cannot_be_restored(tmp_path, monkeypatch):
    from pathlib import Path

    skills = tmp_path / "skills"
    _install_entry(skills)
    rename = Path.rename

    def failing_rename(self, target):
        if Path(target).name == "repo2test":
            raise OSError("busy")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(SystemExit, match="previous entry is at .*repo2test-previous"):
        _install_entry(skills, "--update")
    assert (skills / ".repo2test-previous/SKILL.md").is_file()
    assert not (skills / ".repo2test-update").exists()


def test_entry_install_removes_directories_left_by_an_interrupted_update(tmp_path):
    skills = tmp_path / "skills"
    for leftover in (".repo2test-update", ".repo2test-previous"):
        (skills / leftover).mkdir(parents=True)
        (skills / leftover / "SKILL.md").write_text("stale")
    _install_entry(skills)
    assert sorted(p.name for p in skills.iterdir()) == ["repo2test"]


def test_cleanup_is_kept_when_a_created_resource_returned_an_undecodable_body(respx_mock):
    import httpx

    respx_mock.post("http://shop/login").respond(200, json={"token": "t"})
    respx_mock.post("http://shop/orders").mock(side_effect=httpx.DecodingError("bad gzip"))
    result = run_case(_order_case(), profile=_staging(), scope_dir=None)
    assert result.teardown_errors and "cleanup failed" in result.error_message
    assert result.residual_data and "skipped" not in result.error_message


def test_cleanup_is_kept_when_a_setup_helper_started(helper_suite, respx_mock):
    suite, _ = helper_suite
    (suite / "_helpers/creates.py").write_text(
        "def create(ctx):\n    raise RuntimeError('created, then failed')\n"
    )
    case = _order_case(setup={"python": [{"call": "helpers.creates.create"}]})
    case.steps[0].extract = {"order_id": "$.id"}
    result = run_with_helpers(case, _staging(), suite)
    assert result.outcome == CaseOutcome.ERROR
    assert result.teardown_errors and "skipped" not in (result.error_message or "")


def test_write_blocker_lists_every_write_reason(respx_mock):
    case = model(
        setup={"steps": [{"name": "login", "request": {"method": "POST", "path": "/login"}}]},
        teardown={"steps": [{"name": "del", "request": {"method": "DELETE", "path": "/o"}}]},
    )
    result = run_case(case, profile=production(), scope_dir=None)
    assert "setup step 'login' uses POST" in result.error_message
    assert "teardown step 'del' uses DELETE" in result.error_message
