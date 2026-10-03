import pytest

from apitest.profile import Profile
from apitest.runner.lifecycle import CaseOutcome, run_case
from apitest.schema.case_v1 import Case


def test_lifecycle_pass(respx_mock):
    respx_mock.post("http://example-payment/api/x").respond(200, json={"ok": True})
    case = Case.model_validate(
        {
            "schema": "v1",
            "mutates": False,
            "name": "t",
            "service": "example-payment",
            "steps": [
                {
                    "name": "do",
                    "request": {"method": "POST", "path": "/api/x"},
                    "assert": {"status": 200, "json": {"ok": True}},
                },
            ],
        }
    )
    profile = Profile(
        name="t",
        environment="non-production",
        services={"example-payment": {"base_url": "http://example-payment", "auth_mode": "jwt"}},
    )
    result = run_case(case, profile=profile, scope_dir=None)
    assert result.outcome == CaseOutcome.PASS


def test_lifecycle_fail_runs_teardown(respx_mock):
    respx_mock.post("http://example-payment/api/x").respond(500, json={})
    case = Case.model_validate(
        {
            "schema": "v1",
            "mutates": False,
            "name": "t",
            "service": "example-payment",
            "steps": [
                {
                    "name": "do",
                    "request": {"method": "POST", "path": "/api/x"},
                    "assert": {"status": 200},
                },
            ],
        }
    )
    profile = Profile(
        name="t",
        environment="non-production",
        services={"example-payment": {"base_url": "http://example-payment", "auth_mode": "jwt"}},
    )
    result = run_case(case, profile=profile, scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert result.teardown_ran is True


def test_lifecycle_error_on_setup_break():
    case = Case.model_validate(
        {
            "schema": "v1",
            "mutates": True,
            "name": "t",
            "service": "example-payment",
            "setup": {"python": [{"call": "helpers.does_not_exist.fn"}]},
            "data": {"isolated": True, "cleanup": "Attempt the declared cleanup helper."},
            "teardown": {"python": [{"call": "helpers.does_not_exist.cleanup"}]},
            "steps": [
                {
                    "name": "x",
                    "request": {"method": "GET", "path": "/x"},
                    "assert": {"status": 200},
                },
            ],
        }
    )
    profile = Profile(
        name="t",
        environment="non-production",
        services={"example-payment": {"base_url": "http://x", "auth_mode": "jwt"}},
    )
    result = run_case(case, profile=profile, scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR


def test_row_matches_normalises_db_types_against_yaml_values():
    from datetime import datetime
    from decimal import Decimal

    from apitest.runner.lifecycle import _row_matches

    row = {"id": 42, "amount": Decimal("9.50"), "at": datetime(2026, 1, 2, 3, 4, 5), "ok": 1}
    assert _row_matches(row, {"id": "42"})
    assert _row_matches(row, {"id": 42})
    assert _row_matches(row, {"amount": 9.5})
    assert _row_matches(row, {"at": "2026-01-02 03:04:05"})
    assert _row_matches(row, {"ok": True})
    assert not _row_matches(row, {"id": 43})


# ── F6: HTTP exchange log ────────────────────────────────────────────────


def _profile() -> Profile:
    return Profile(
        name="t",
        environment="non-production",
        services={"example-payment": {"base_url": "http://example-payment"}},
        http={"retries": {"count": 0}},
    )


def _case(**extra):
    owns_data = bool(extra.get("setup") or extra.get("teardown"))
    return Case.model_validate(
        {
            "schema": "v1",
            "mutates": None if owns_data else False,
            "name": "t",
            "service": "example-payment",
            "data": (
                {"isolated": True, "cleanup": "Fixture-owned data is tracked and deleted."}
                if owns_data else {}
            ),
            "steps": [
                {
                    "name": "do",
                    "request": {"method": "POST", "path": "/api/x", "body": {"q": 1}},
                    "assert": {"status": 200},
                }
            ],
            **extra,
        }
    )


def test_failed_case_result_carries_redacted_http_log(respx_mock):
    from apitest.taint import TaintedStr

    TaintedStr("hunter2", source="DB_PASS")
    respx_mock.post("http://example-payment/api/x").respond(500, json={"pw": "hunter2"})
    result = run_case(_case(), profile=_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert len(result.http_log) == 1
    entry = result.http_log[0]
    assert (entry["method"], entry["url"], entry["status"]) == (
        "POST",
        "http://example-payment/api/x",
        500,
    )
    assert entry["request_body"] == {"q": 1}
    assert "hunter2" not in entry["response_body"] and "***" in entry["response_body"]


def test_format_http_log_is_readable():
    from apitest.http_client import format_http_log

    text = format_http_log(
        [
            {
                "service": "s",
                "method": "POST",
                "url": "http://h/api/x",
                "status": 500,
                "request_headers": {},
                "response_headers": {},
                "request_body": {"q": 1},
                "response_body": '{"error":"boom"}',
                "retries": ["attempt 1: status 503"],
            }
        ]
    )
    assert "POST http://h/api/x -> 500" in text
    assert '{"q": 1}' in text and '{"error":"boom"}' in text and "attempt 1: status 503" in text


# ── F7: verify wait/poll ─────────────────────────────────────────────────


class _FakeHandle:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def query(self, sql, *params):
        self.calls += 1
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class _FakeDbClient:
    handle: _FakeHandle

    def __init__(self, profile):
        pass

    def for_service(self, service):
        return type(self).handle

    def for_verification(self, service):
        return type(self).handle

    def close_all(self):
        pass


def _db_profile() -> Profile:
    p = _profile()
    p.databases = {"example-payment": {"dsn": "mysql://u:p@h/d"}}
    return p


def _db_case(verify_db):
    return _case(verify={"db": [verify_db]})


def test_verify_db_wait_polls_until_rows_appear(monkeypatch, respx_mock):
    import apitest.runner.lifecycle as lc

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    _FakeDbClient.handle = _FakeHandle([[], [], [{"id": 1}]])
    monkeypatch.setattr(lc, "DbClient", _FakeDbClient)
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    case = _db_case(
        {
            "sql": "SELECT 1",
            "expect_count": 1,
            "wait": {"timeout_s": 5, "interval_s": 0.01},
        }
    )
    result = run_case(case, profile=_db_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    assert _FakeDbClient.handle.calls == 3


def test_verify_db_without_wait_checks_once(monkeypatch, respx_mock):
    import apitest.runner.lifecycle as lc

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    _FakeDbClient.handle = _FakeHandle([[], [{"id": 1}]])
    monkeypatch.setattr(lc, "DbClient", _FakeDbClient)
    case = _db_case({"sql": "SELECT 1", "expect_count": 1})
    result = run_case(case, profile=_db_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert _FakeDbClient.handle.calls == 1


def test_verify_db_wait_times_out_with_message(monkeypatch, respx_mock):
    import apitest.runner.lifecycle as lc

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    _FakeDbClient.handle = _FakeHandle([[]])
    monkeypatch.setattr(lc, "DbClient", _FakeDbClient)
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    case = _db_case(
        {
            "sql": "SELECT 1",
            "expect_count": 1,
            "wait": {"timeout_s": 0.05, "interval_s": 0.01},
        }
    )
    result = run_case(case, profile=_db_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert "expected 1, got 0" in result.error_message and "after waiting" in result.error_message


class _FakeMq:
    drains: list
    calls = 0

    def __init__(self, profile, *, case_id_short, name="default"):
        pass

    def declare_sniffer(self, *, alias, exchange, routing_key):
        pass

    def drain(self, alias):
        type(self).calls += 1
        return type(self).drains.pop(0) if type(self).drains else []

    def close(self):
        pass


def _mq_case(verify_mq):
    return _case(
        setup={"rabbitmq": {"g": [{"exchange": "ex", "routing_key": "rk", "as": "orders"}]}},
        verify={"rabbitmq": verify_mq},
    )


def test_verify_mq_wait_accumulates_drained_messages_across_entries(monkeypatch, respx_mock):
    import apitest.runner.lifecycle as lc
    from apitest.fixtures.rabbitmq import CapturedMessage

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    msg = CapturedMessage(body=b'{"id": 7}', routing_key="rk")
    _FakeMq.drains = [[], [msg]]
    _FakeMq.calls = 0
    monkeypatch.setattr(lc, "MqClient", _FakeMq)
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    profile = _profile()
    profile.rabbitmq = {"default": {"url": "amqp://x"}}
    case = _mq_case(
        [
            {
                "sniffer": "orders",
                "count": 1,
                "match": {"body.id": 7},
                "wait": {"timeout_s": 5, "interval_s": 0.01},
            },
            {"sniffer": "orders", "count": 1},  # same sniffer: must see the already-drained message
        ]
    )
    result = run_case(case, profile=profile, scope_dir=None)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    # entry 1: empty poll + message poll; entry 2: one re-drain (picks up late messages)
    assert _FakeMq.calls == 3


# ── F5: redis seeding for the SUT + teardown delete ──────────────────────


class _FakeRawRedis:
    def __init__(self):
        self.store: dict[str, object] = {}
        self.deleted: list[str] = []

    def set(self, k, v, ex=None):
        self.store[k] = v

    def hset(self, k, mapping=None):
        self.store[k] = dict(mapping or {})

    def expire(self, k, ttl):
        pass

    def get(self, k):
        v = self.store.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, *keys):
        return sum(k in self.store for k in keys)

    def ttl(self, k):
        return -1

    def delete(self, *keys):
        self.deleted.extend(keys)
        return sum(self.store.pop(k, None) is not None for k in keys)

    def close(self):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    import redis as redis_lib

    raw = _FakeRawRedis()
    monkeypatch.setattr(redis_lib.Redis, "from_url", classmethod(lambda cls, url: raw))
    return raw


def _redis_profile() -> Profile:
    p = _profile()
    p.redis = {"default": {"url": "redis://h/0"}}
    return p


def test_setup_redis_unprefixed_seeds_key_the_sut_can_read_and_cleans_it(
    fake_redis, respx_mock, tmp_path
):
    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    seed = tmp_path / "fixtures/redis/seed.yaml"
    seed.parent.mkdir(parents=True)
    seed.write_text(
        "set:\n"
        "  - { key: 'session:${ctx.case.id_short}', value: tok, unprefixed: true }\n"
        "  - { key: 'scratch', value: x }\n"
    )
    seen: dict = {}
    case = _case(setup={"redis": ["fixtures/redis/seed.yaml"]})

    orig_set = fake_redis.set

    def spy_set(k, v, ex=None):
        seen.setdefault("keys", []).append(k)
        orig_set(k, v, ex=ex)

    fake_redis.set = spy_set
    result = run_case(case, profile=_redis_profile(), scope_dir=tmp_path)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    session_key = [k for k in seen["keys"] if k.startswith("session:")]
    assert len(session_key) == 1 and not session_key[0].startswith("t:")
    assert any(k.startswith("t:") and k.endswith(":scratch") for k in seen["keys"])
    assert set(fake_redis.deleted) == set(seen["keys"])  # both tracked and cleaned


def test_teardown_redis_delete_removes_sut_written_keys(fake_redis, respx_mock):
    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    case = _case(teardown={"redis": {"delete": ["cache:${ctx.case.id_short}", "other"]}})
    result = run_case(case, profile=_redis_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    assert "other" in fake_redis.deleted
    assert any(k.startswith("cache:") and not k.startswith("t:") for k in fake_redis.deleted)


# ── F8: count_min / count_max are enforced ───────────────────────────────


def _two_matching_messages():
    from apitest.fixtures.rabbitmq import CapturedMessage

    return [CapturedMessage(body=b'{"id": 7}', routing_key="rk") for _ in range(2)]


def _mq_profile() -> Profile:
    profile = _profile()
    profile.rabbitmq = {"default": {"url": "amqp://x"}}
    return profile


def test_verify_mq_count_min_is_enforced(monkeypatch, respx_mock):
    import apitest.runner.lifecycle as lc

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    monkeypatch.setattr(lc, "MqClient", _FakeMq)
    _FakeMq.drains = [_two_matching_messages()]
    ok = _mq_case([{"sniffer": "orders", "count_min": 1, "match": {"body.id": 7}}])
    assert run_case(ok, profile=_mq_profile(), scope_dir=None).outcome == CaseOutcome.PASS
    _FakeMq.drains = [_two_matching_messages()]
    short = _mq_case([{"sniffer": "orders", "count_min": 3}])
    result = run_case(short, profile=_mq_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert "at least 3" in result.error_message and "got 2" in result.error_message


def test_verify_mq_count_max_fails_when_too_many_matched(monkeypatch, respx_mock):
    import apitest.runner.lifecycle as lc

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    _FakeMq.drains = [_two_matching_messages()]
    monkeypatch.setattr(lc, "MqClient", _FakeMq)
    case = _mq_case([{"sniffer": "orders", "count_max": 1}])
    result = run_case(case, profile=_mq_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert "at most 1" in result.error_message and "got 2" in result.error_message


# ── F13: teardown runs after a SETUP failure ─────────────────────────────


def test_setup_failure_still_runs_teardown(fake_redis, respx_mock):
    case = _case(
        setup={"python": [{"call": "helpers.nope.fn"}]},  # no resolver -> setup error
        teardown={"redis": {"delete": ["leftover:${ctx.case.id_short}"]}},
    )
    result = run_case(case, profile=_redis_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.ERROR
    assert result.error_message.startswith("SETUP failed")
    assert result.teardown_ran is True
    assert any(k.startswith("leftover:") for k in fake_redis.deleted)


# ── F9: secrets are redacted from error messages ─────────────────────────


def test_error_message_is_redacted(respx_mock):
    from apitest.taint import TaintedStr

    TaintedStr("hunter2", source="DB_PASS")
    respx_mock.post("http://example-payment/api/x").respond(200, json={"pw": "hunter2"})
    case = Case.model_validate(
        {
            "schema": "v1",
            "mutates": False,
            "name": "t",
            "service": "example-payment",
            "steps": [
                {
                    "name": "do",
                    "request": {"method": "POST", "path": "/api/x"},
                    "assert": {"status": 200, "json": {"pw": "other"}},
                }
            ],
        }
    )
    result = run_case(case, profile=_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.FAIL
    assert "hunter2" not in result.error_message and "***" in result.error_message


# ── F11: case-level headers ──────────────────────────────────────────────


def test_case_level_headers_are_rendered_per_case(respx_mock):
    route = respx_mock.post("http://example-payment/api/x").respond(200, json={})
    case = Case.model_validate(
        {
            "schema": "v1",
            "mutates": False,
            "name": "t",
            "service": "example-payment",
            "ids": {"token": {"kind": "string", "prefix": "T-"}},
            "headers": {"Authorization": "Bearer ${ctx.case.token}"},
            "steps": [
                {
                    "name": "do",
                    "request": {"method": "POST", "path": "/api/x"},
                    "assert": {"status": 200},
                }
            ],
        }
    )
    result = run_case(case, profile=_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    auth = route.calls.last.request.headers["authorization"]
    assert auth.startswith("Bearer T-") and len(auth) == len("Bearer T-") + 12


def test_no_builtin_project_specific_auth_helper():
    import importlib.util

    assert importlib.util.find_spec("apitest.helpers.auth") is None


# ── README self-tests D2 / D4 / D6 ───────────────────────────────────────


def test_d2_sniffer_count_filters_by_match_not_raw_queue_depth(monkeypatch, respx_mock):
    import apitest.runner.lifecycle as lc
    from apitest.fixtures.rabbitmq import CapturedMessage

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    monkeypatch.setattr(lc, "MqClient", _FakeMq)
    _FakeMq.drains = [
        [
            CapturedMessage(body=b'{"id": 7}', routing_key="rk"),
            CapturedMessage(body=b'{"id": 8}', routing_key="rk"),
            CapturedMessage(body=b'{"id": 9}', routing_key="rk"),
        ]
    ]
    case = _mq_case([{"sniffer": "orders", "count": 1, "match": {"body.id": 8}}])
    result = run_case(case, profile=_mq_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.PASS, result.error_message


class _FakeRaceMq:
    """Only messages published after the sniffer was declared are captured."""

    declared = False
    published: list = []

    def __init__(self, profile, *, case_id_short, name="default"):
        type(self).declared = False
        type(self).published = []

    def declare_sniffer(self, *, alias, exchange, routing_key):
        type(self).declared = True

    @classmethod
    def publish(cls, msg):
        if cls.declared:
            cls.published.append(msg)

    def drain(self, alias):
        out, type(self).published = list(type(self).published), []
        return out

    def close(self):
        pass


def test_d4_sniffer_captures_messages_emitted_during_execute(monkeypatch, respx_mock):
    import httpx

    import apitest.runner.lifecycle as lc
    from apitest.fixtures.rabbitmq import CapturedMessage

    monkeypatch.setattr(lc, "MqClient", _FakeRaceMq)

    def handler(request: httpx.Request) -> httpx.Response:
        # the service publishes while handling the request
        _FakeRaceMq.publish(CapturedMessage(body=b'{"id": 1}', routing_key="rk"))
        return httpx.Response(200, json={})

    respx_mock.post("http://example-payment/api/x").mock(side_effect=handler)
    case = _mq_case([{"sniffer": "orders", "count": 1}])
    result = run_case(case, profile=_mq_profile(), scope_dir=None)
    assert result.outcome == CaseOutcome.PASS, result.error_message


def test_d6_redis_cleanup_deletes_tracked_keys_without_scan(fake_redis, respx_mock, tmp_path):
    from apitest.fixtures.redis import RedisClient

    respx_mock.post("http://example-payment/api/x").respond(200, json={})
    seed = tmp_path / "fixtures/redis/seed.yaml"
    seed.parent.mkdir(parents=True)
    seed.write_text("set:\n  - { key: 'a', value: 1 }\n  - { key: 'b', value: 2 }\n")
    scans: list = []
    fake_redis.scan = lambda **kw: scans.append(kw) or (0, [])
    case = _case(setup={"redis": ["fixtures/redis/seed.yaml"]})
    result = run_case(case, profile=_redis_profile(), scope_dir=tmp_path)
    assert result.outcome == CaseOutcome.PASS, result.error_message
    assert sorted(k.split(":")[-1] for k in fake_redis.deleted) == ["a", "b"]
    assert scans == []
    assert not hasattr(RedisClient, "cleanup_by_scan")
