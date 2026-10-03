import httpx
import pytest

from apitest.profile import Profile, load_profile
from apitest.runner.lifecycle import CaseOutcome, run_case
from apitest.schema.case_v1 import Case


def case(**changes):
    return Case.model_validate(
        {
            "schema": "v1",
            "name": "probe",
            "service": "orders",
            "steps": [
                {
                    "name": "read",
                    "request": {"method": "GET", "path": "/1"},
                    "assert": {"status": 200},
                }
            ],
            **changes,
        }
    )


def profile(**changes):
    return Profile(name="stage", services={"orders": {"base_url": "http://orders"}}, **changes)


@pytest.mark.parametrize(
    "sql", ["TRUNCATE TABLE t", "/* comment */ DELETE FROM t", "SELECT evil()"]
)
def test_verification_sql_cannot_bypass_production_guard(sql, monkeypatch):
    from unittest.mock import Mock

    http = Mock()
    db = Mock()
    monkeypatch.setattr("apitest.runner.lifecycle.HttpClient", http)
    monkeypatch.setattr("apitest.runner.lifecycle.DbClient", db)
    result = run_case(
        case(verify={"db": [{"sql": sql, "expect_count": 0}]}),
        profile=profile(environment="production", databases={"orders": {"dsn": "mysql://db"}}),
        scope_dir=None,
    )
    assert result.outcome == CaseOutcome.BLOCKED
    assert "verify.db.sql" in result.error_message
    http.assert_not_called()
    db.assert_not_called()


def test_rendered_sql_is_rechecked_before_query(monkeypatch, respx_mock):
    from unittest.mock import MagicMock

    db = MagicMock()
    monkeypatch.setattr("apitest.runner.lifecycle.DbClient", lambda _: db)
    respx_mock.get("http://orders/1").respond(200)
    result = run_case(
        case(verify={"db": [{"sql": "SELECT ${ctx.profile.test_data.value}", "expect_count": 0}]}),
        profile=profile(
            environment="production",
            databases={"orders": {"dsn": "mysql://db"}},
            test_data={"value": "1; TRUNCATE TABLE t"},
        ),
        scope_dir=None,
    )
    assert result.outcome == CaseOutcome.ERROR
    assert "verify.db.sql" in result.error_message
    db.for_verification.return_value.query.assert_not_called()


def test_database_close_failure_does_not_skip_http_close(monkeypatch, respx_mock):
    from unittest.mock import MagicMock

    from apitest.http_client import HttpClient

    db = MagicMock()
    db.for_verification.return_value.query.return_value = [{"id": 1}]
    db.close_all.side_effect = RuntimeError("db connection cleanup failed")
    monkeypatch.setattr("apitest.runner.lifecycle.DbClient", lambda _: db)
    http = HttpClient(profile())
    close = MagicMock(wraps=http.close)
    http.close = close
    monkeypatch.setattr("apitest.runner.lifecycle.HttpClient", lambda _: http)
    respx_mock.get("http://orders/1").respond(200)
    result = run_case(
        case(verify={"db": [{"sql": "SELECT 1", "expect_count": 1}]}),
        profile=profile(databases={"orders": {"dsn": "mysql://db"}}),
        scope_dir=None,
    )
    assert result.outcome == CaseOutcome.ERROR and result.business_outcome == "PASS"
    assert any("db close: db connection cleanup failed" in e for e in result.teardown_errors)
    close.assert_called_once()


@pytest.mark.parametrize(
    "expression",
    [
        "ctx.profile.test_data['account']",
        'ctx.profile.test_data["account"]',
        "ctx['profile']['test_data']['account']",
        "ctx.profile.test_data.account",
        "(ctx.profile.test_data|attr('account'))",
    ],
)
def test_indexed_test_data_is_resolved_and_missing_values_block_before_http(expression, respx_mock):
    route = respx_mock.get("http://orders/1", params={"account": "7"}).respond(200)
    c = case(
        steps=[
            {
                "name": "read",
                "request": {
                    "method": "GET",
                    "path": "/1",
                    "params": {"account": "${" + expression + "}"},
                },
                "assert": {"status": 200},
            }
        ]
    )
    missing = run_case(c, profile=profile(), scope_dir=None)
    assert missing.outcome == CaseOutcome.BLOCKED and "test_data.account" in missing.error_message
    assert not route.called
    ok = run_case(
        c, profile=profile(test_data={"account": 7, "unused": "${NEVER_RESOLVE}"}), scope_dir=None
    )
    assert ok.outcome == CaseOutcome.PASS, ok.error_message
    assert route.called


def test_dynamic_data_requires_declaration_and_template_includes_are_scanned(tmp_path):
    from apitest.preflight import prepare_profile

    c = case(headers={"x": "${ctx.profile.test_data[ctx.key]}"})
    _, missing = prepare_profile(c, profile(), scope_dir=tmp_path)
    assert any("requires.test_data" in m for m in missing)
    c.requires.test_data = ["account"]
    resolved, missing = prepare_profile(c, profile(test_data={"account": 7}), scope_dir=tmp_path)
    assert missing == [] and resolved.test_data == {"account": 7}
    (tmp_path / "body.j2").write_text("{% include 'part.j2' %}")
    (tmp_path / "part.j2").write_text("{{ ctx.profile.test_data['account'] }}")
    c = case(
        steps=[
            {"name": "read", "request": {"method": "GET", "path": "/1", "body_template": "body.j2"}}
        ]
    )
    _, missing = prepare_profile(c, profile(), scope_dir=tmp_path)
    assert any("test_data.account" in m for m in missing)
    (tmp_path / "part.j2").write_text("{{ broken")
    result = run_case(c, profile=profile(), scope_dir=tmp_path)
    assert (
        result.outcome == CaseOutcome.BLOCKED
        and "invalid dependency template" in result.error_message
    )


def test_actual_mq_close_failure_reports_queue_and_continues_cleanup(monkeypatch, respx_mock):
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.close.side_effect = RuntimeError("broker close failed")
    monkeypatch.setattr("apitest.fixtures.rabbitmq.pika.BlockingConnection", lambda _: conn)
    redis = MagicMock()
    monkeypatch.setattr("apitest.runner.lifecycle.RedisClient", lambda *a, **kw: redis)
    respx_mock.get("http://orders/1").respond(200)
    result = run_case(
        case(
            setup={"rabbitmq": {"g": [{"as": "orders", "exchange": "ex", "routing_key": "rk"}]}},
            requires={"redis": True},
            data={"isolated": True, "cleanup": "Check owned resources"},
        ),
        profile=profile(
            environment="non-production",
            rabbitmq={"default": {"url": "amqp://localhost"}},
            redis={"default": {"url": "redis://localhost"}},
        ),
        scope_dir=None,
    )
    assert result.outcome == CaseOutcome.ERROR and result.business_outcome == "PASS"
    assert any("broker close failed" in e for e in result.teardown_errors)
    queue = result.residual_data["rabbitmq_queues"]["orders"]
    assert queue.startswith("apitest.sniffer.orders.")
    assert "owning connection" in result.cleanup_guidance
    conn.close.assert_called_once()
    redis.cleanup.assert_called_once()
    redis.close.assert_called_once()


def test_unused_dependencies_and_roles_do_not_block(tmp_path, respx_mock):
    (tmp_path / "stage.yaml").write_text(
        "services:\n  orders:\n    base_url: http://orders\n"
        '    roles:\n      unused: {headers: {Authorization: "${UNUSED_TOKEN}"}}\n'
        'databases:\n  orders: {dsn: "${UNUSED_DSN}"}\n'
        'redis:\n  default: {url: "${UNUSED_REDIS}"}\n'
    )
    p = load_profile("stage", tmp_path, resolve=False)
    respx_mock.get("http://orders/1").respond(200)
    assert run_case(case(), profile=p, scope_dir=None).outcome == CaseOutcome.PASS


def test_missing_declared_verification_is_blocked_before_http(respx_mock):
    route = respx_mock.get("http://orders/1").respond(200)
    result = run_case(
        case(verify={"redis": [{"key": "x", "exists": True}]}), profile=profile(), scope_dir=None
    )
    assert result.outcome == CaseOutcome.BLOCKED
    assert "redis" in result.error_message
    assert not route.called


@pytest.mark.parametrize("environment", [None, "production"])
def test_mutations_require_nonproduction(environment, respx_mock):
    route = respx_mock.post("http://orders/1").respond(200)
    c = case(
        steps=[{"name": "create", "request": {"method": "POST", "path": "/1"}}],
        data={"isolated": True, "cleanup": "Delete this order"},
        teardown={"steps": [{"name": "clean", "request": {"method": "DELETE", "path": "/1"}}]},
    )
    result = run_case(c, profile=profile(environment=environment), scope_dir=None)
    assert result.outcome == CaseOutcome.BLOCKED
    assert "non-production" in result.error_message
    assert not route.called


def test_writes_without_cleanup_are_blocked(respx_mock):
    c = case(steps=[{"name": "create", "request": {"method": "POST", "path": "/1"}}])
    result = run_case(c, profile=profile(environment="non-production"), scope_dir=None)
    assert result.outcome == CaseOutcome.BLOCKED
    assert "cleanup" in result.error_message


@pytest.mark.parametrize("classification", [None, "production", "non-production"])
def test_child_profile_must_authorize_its_own_write_target(tmp_path, respx_mock, classification):
    (tmp_path / "base.yaml").write_text(
        "environment: non-production\nservices:\n  orders: {base_url: http://staging}\n"
    )
    (tmp_path / "child.yaml").write_text(
        "extends: base.yaml\nservices:\n  orders: {base_url: http://orders}\n"
        + (f"environment: {classification}\n" if classification else "")
    )
    request = respx_mock.put("http://orders/1").respond(201)
    cleanup = respx_mock.delete("http://orders/1").respond(204)
    c = case(
        steps=[{"name": "write", "request": {"method": "PUT", "path": "/1"}}],
        data={"isolated": True, "cleanup": "delete this case's record"},
        teardown={"steps": [{"name": "clean", "request": {"method": "DELETE", "path": "/1"}}]},
    )
    p = load_profile("child", tmp_path, resolve=False)
    outcome = run_case(c, profile=p, scope_dir=None).outcome
    assert p.environment == classification
    assert outcome == (
        CaseOutcome.PASS if classification == "non-production" else CaseOutcome.BLOCKED
    )
    assert request.called == cleanup.called == (classification == "non-production")


@pytest.mark.parametrize("phase", ["setup", "verify", "teardown"])
@pytest.mark.parametrize("mutates", [None, False])
def test_helpers_are_guarded_before_import_even_with_readonly_case_flag(
    tmp_path, respx_mock, phase, mutates
):
    from apitest.escape import HelperResolver

    helper = tmp_path / "_helpers/writer.py"
    helper.parent.mkdir()
    sentinel = tmp_path / "imported"
    helper.write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
        "def write(http): http.post('orders', '/side-effect')\n"
    )
    if phase == "teardown" and mutates is False:
        with pytest.raises(ValueError, match="cannot be combined with teardown"):
            case(mutates=mutates, **{phase: {"python": [{"call": "helpers.writer.write"}]}})
        assert not sentinel.exists()
        return
    c = case(mutates=mutates, **{phase: {"python": [{"call": "helpers.writer.write"}]}})
    result = run_case(
        c,
        profile=profile(environment="production"),
        scope_dir=tmp_path,
        helper_resolver=HelperResolver(tmp_path),
        case_yaml_path=tmp_path / "case_probe.yaml",
    )
    assert result.outcome == CaseOutcome.BLOCKED
    assert "non-production" in result.error_message
    # A readonly declaration waives write prerequisites, never the environment.
    assert ("data.isolated" in result.error_message) == (mutates is None)
    assert not sentinel.exists() and not respx_mock.calls


@pytest.mark.parametrize("url", [None, "", "${UNSET_ORDERS_URL}"])
def test_missing_service_url_blocks_before_any_request(url, respx_mock, monkeypatch):
    monkeypatch.delenv("UNSET_ORDERS_URL", raising=False)
    p = Profile(name="local", services={"orders": {"base_url": url}})
    result = run_case(case(), profile=p, scope_dir=None)
    assert result.outcome == CaseOutcome.BLOCKED
    assert "service URL" in result.error_message
    assert not respx_mock.calls


def test_cross_service_identity_extraction_and_cookie_isolation(respx_mock):
    seen = []

    def respond(request):
        seen.append(
            (str(request.url), request.headers.get("authorization"), request.headers.get("cookie"))
        )
        return httpx.Response(200, json={"id": 42}, headers={"Set-Cookie": "session=first; Path=/"})

    respx_mock.get("http://shared/orders/1").mock(side_effect=respond)
    respx_mock.get("http://shared/payments/42").mock(side_effect=respond)
    respx_mock.get("http://shared/orders/admin").mock(side_effect=respond)
    p = Profile(
        name="stage",
        services={
            "orders": {
                "base_url": "http://shared/orders",
                "headers": {"Authorization": "order-token"},
                "roles": {"admin": {"headers": {"Authorization": "admin-token"}}},
            },
            "payments": {
                "base_url": "http://shared/payments",
                "headers": {"Authorization": "payment-token"},
            },
        },
    )
    c = case(
        headers={"Authorization": "case-token"},
        steps=[
            {
                "name": "order",
                "request": {"method": "GET", "path": "/1"},
                "extract": {"id": "$.id"},
            },
            {
                "name": "payment",
                "service": "payments",
                "request": {"method": "GET", "path": "/${ctx.id}"},
            },
            {"name": "admin", "role": "admin", "request": {"method": "GET", "path": "/admin"}},
            {"name": "order_again", "request": {"method": "GET", "path": "/1"}},
        ],
    )
    assert run_case(c, profile=p, scope_dir=None).outcome == CaseOutcome.PASS
    assert [r[1] for r in seen] == ["case-token", "payment-token", "admin-token", "case-token"]
    assert [r[2] for r in seen] == [None, None, None, "session=first"]


def test_cleanup_after_business_failure_keeps_both_results(respx_mock):
    respx_mock.post("http://orders/").respond(201, json={"id": 123})
    respx_mock.get("http://orders/123").respond(500)
    cleanup = respx_mock.delete("http://orders/123").respond(503)
    c = case(
        setup={
            "steps": [
                {
                    "name": "create",
                    "request": {"method": "POST", "path": "/"},
                    "extract": {"order_id": "$.id"},
                }
            ]
        },
        steps=[
            {
                "name": "read",
                "request": {"method": "GET", "path": "/${ctx.order_id}"},
                "assert": {"status": 200},
            }
        ],
        data={"isolated": True, "cleanup": "Delete order_id using the orders API"},
        teardown={
            "steps": [
                {
                    "name": "clean",
                    "request": {"method": "DELETE", "path": "/${ctx.order_id}"},
                    "assert": {"status": 204},
                }
            ]
        },
        known_defects=[{"ref": "BUG-1", "step": "read", "at": "status"}],
    )
    result = run_case(c, profile=profile(environment="non-production"), scope_dir=None)
    assert cleanup.called
    assert result.outcome == CaseOutcome.ERROR
    assert result.business_outcome == "FAIL"
    assert result.failed_step == "read"
    assert result.residual_data["extracts"]["order_id"] == 123
    assert result.teardown_errors and result.cleanup_guidance


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (400, {"ok": True}, CaseOutcome.XFAIL),
        (200, {"ok": False}, CaseOutcome.FAIL),
        (200, {"ok": True}, CaseOutcome.XPASS),
    ],
)
def test_strict_defect_matches_only_the_selected_assertion(respx_mock, status, body, expected):
    respx_mock.get("http://orders/1").respond(status, json=body)
    c = case(
        steps=[
            {
                "name": "read",
                "request": {"method": "GET", "path": "/1"},
                "assert": {"status": 200, "json": {"ok": True}},
            }
        ],
        known_defects=[{"ref": "BUG-1", "step": "read", "at": "status", "env": "stage"}],
    )
    assert run_case(c, profile=profile(), scope_dir=None).outcome == expected


def test_transport_failure_does_not_match_known_defect(respx_mock):
    respx_mock.get("http://orders/1").mock(side_effect=httpx.ConnectError("offline"))
    c = case(known_defects=[{"ref": "BUG-1", "step": "read"}])
    assert run_case(c, profile=profile(), scope_dir=None).outcome == CaseOutcome.ERROR


def test_initialization_error_is_a_result(monkeypatch):
    import apitest.runner.lifecycle as lifecycle

    def broken(*args, **kwargs):
        raise RuntimeError("DB refused")

    monkeypatch.setattr(lifecycle, "DbClient", broken)
    c = case(verify={"db": [{"sql": "SELECT 1", "expect_count": 1}]})
    result = run_case(
        c, profile=profile(databases={"orders": {"dsn": "mysql://x"}}), scope_dir=None
    )
    assert result.outcome == CaseOutcome.ERROR
    assert "LOAD failed" in result.error_message


def test_unused_default_credentials_do_not_block_named_role(respx_mock):
    p = Profile(
        name="stage",
        services={
            "orders": {
                "base_url": "http://orders",
                "headers": {"Authorization": "${UNUSED_DEFAULT_TOKEN}"},
                "roles": {"reader": {"headers": {"Authorization": "reader-credential"}}},
            }
        },
    )
    c = case(
        steps=[
            {
                "name": "read",
                "role": "reader",
                "request": {"method": "GET", "path": "/1"},
                "assert": {"status": 200},
            }
        ]
    )
    respx_mock.get("http://orders/1").respond(200)
    assert run_case(c, profile=p, scope_dir=None).outcome == CaseOutcome.PASS


def test_missing_cleanup_fixture_blocks_before_any_request(tmp_path, respx_mock):
    request = respx_mock.get("http://orders/1").respond(200)
    c = case(
        teardown={"db": ["missing.sql"]}, data={"isolated": True, "cleanup": "delete owned row"}
    )
    result = run_case(
        c,
        profile=profile(
            environment="non-production", databases={"orders": {"dsn": "mysql://invalid"}}
        ),
        scope_dir=tmp_path,
    )
    assert result.outcome == CaseOutcome.BLOCKED
    assert "fixture missing.sql" in result.error_message
    assert not request.called


def test_setup_assertion_failure_preserves_created_id_for_cleanup(respx_mock):
    respx_mock.post("http://orders/").respond(200, json={"id": 7})
    deletion = respx_mock.delete("http://orders/7").respond(204)
    c = case(
        setup={
            "steps": [
                {
                    "name": "create",
                    "request": {"method": "POST", "path": "/"},
                    "extract": {"id": "$.id"},
                    "assert": {"status": 201},
                }
            ]
        },
        teardown={
            "steps": [{"name": "clean", "request": {"method": "DELETE", "path": "/${ctx.id}"}}]
        },
        data={"isolated": True, "cleanup": "delete extracted id"},
    )
    result = run_case(c, profile=profile(environment="non-production"), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.business_outcome == "ERROR"
    assert deletion.called and result.teardown_errors == []


def test_cleanup_http_error_is_not_silently_accepted(respx_mock):
    respx_mock.get("http://orders/1").respond(200)
    respx_mock.delete("http://orders/1").respond(500)
    c = case(
        teardown={"steps": [{"name": "clean", "request": {"method": "DELETE", "path": "/1"}}]},
        data={"isolated": True, "cleanup": "delete test-owned order 1"},
    )
    result = run_case(c, profile=profile(environment="non-production"), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.business_outcome == "PASS"
    assert result.teardown_errors


def test_readonly_flag_does_not_exempt_delete():
    with pytest.raises(ValueError, match="mutates: false cannot be combined with writes"):
        case(
            mutates=False,
            steps=[{"name": "delete", "request": {"method": "DELETE", "path": "/1"}}],
        )


def test_login_credentials_are_redacted_from_reports(respx_mock):
    respx_mock.post("http://orders/login").respond(200, json={"access_token": "fresh-secret-token"})
    respx_mock.get("http://orders/private").respond(403, json={"token": "fresh-secret-token"})
    c = case(
        mutates=False,
        steps=[
            {
                "name": "login",
                "request": {"method": "POST", "path": "/login"},
                "extract": {"token": "$.access_token"},
            },
            {
                "name": "read",
                "request": {
                    "method": "GET",
                    "path": "/private",
                    "headers": {"Authorization": "Bearer ${ctx.token}"},
                },
                "assert": {"status": 200},
            },
        ],
    )
    result = run_case(c, profile=profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert "fresh-secret-token" not in repr(result)


def test_fixture_test_data_is_resolved_without_unused_values(tmp_path):
    from apitest.preflight import prepare_profile

    (tmp_path / "body.json.j2").write_text('{"user": "${ctx.profile.test_data.user}"}')
    c = case(
        mutates=False,
        steps=[
            {
                "name": "query",
                "request": {
                    "method": "POST",
                    "path": "/search",
                    "body_template": "body.json.j2",
                },
            }
        ],
    )
    p = profile(test_data={"user": "fixture-user", "unused": "${UNUSED_FIXTURE_DATA}"})
    resolved, blockers = prepare_profile(c, p, scope_dir=tmp_path)
    assert blockers == []
    assert resolved.test_data == {"user": "fixture-user"}
