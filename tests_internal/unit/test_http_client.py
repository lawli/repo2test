import pytest

from apitest.http_client import HttpClient
from apitest.profile import Profile


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="test",
        services={"example-payment": {"base_url": "http://example-payment", "auth_mode": "jwt"}},
        http={"default_timeout_s": 5, "retries": {"count": 0}},
    )


def test_get_uses_service_base_url(profile: Profile, respx_mock):
    respx_mock.get("http://example-payment/api/x").respond(200, json={"ok": True})
    c = HttpClient(profile)
    r = c.get("example-payment", "/api/x")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_unknown_service_raises(profile: Profile):
    c = HttpClient(profile)
    with pytest.raises(KeyError):
        c.get("nope", "/x")


def test_request_log_records_each_call(profile: Profile, respx_mock):
    respx_mock.get("http://example-payment/api/y").respond(200, json={})
    c = HttpClient(profile)
    c.get("example-payment", "/api/y")
    log = c.drain_log()
    assert len(log) == 1
    assert log[0]["method"] == "GET" and log[0]["service"] == "example-payment"


def _retry_profile() -> Profile:
    return Profile(
        name="test",
        services={"example-payment": {"base_url": "http://example-payment"}},
        http={"retries": {"count": 2, "backoff_s": 0}},
    )


def _raising_route(respx_mock, method, exc):
    import httpx

    calls = {"n": 0}

    def side_effect(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise exc
        return httpx.Response(200, json={})

    getattr(respx_mock, method)("http://example-payment/api/x").mock(side_effect=side_effect)
    return calls


def test_get_retries_read_timeout(respx_mock):
    import httpx

    calls = _raising_route(respx_mock, "get", httpx.ReadTimeout("slow"))
    r = HttpClient(_retry_profile()).get("example-payment", "/api/x")
    assert r.status_code == 200 and calls["n"] == 3


def test_post_does_not_retry_after_request_may_have_been_sent(respx_mock):
    import httpx

    calls = _raising_route(respx_mock, "post", httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        HttpClient(_retry_profile()).post("example-payment", "/api/x", json={})
    assert calls["n"] == 1


def test_post_still_retries_when_connection_never_opened(respx_mock):
    import httpx

    calls = _raising_route(respx_mock, "post", httpx.ConnectError("refused"))
    r = HttpClient(_retry_profile()).post("example-payment", "/api/x", json={})
    assert r.status_code == 200 and calls["n"] == 3


def test_post_does_not_retry_5xx(respx_mock):
    route = respx_mock.post("http://example-payment/api/x").respond(503, json={})
    r = HttpClient(_retry_profile()).post("example-payment", "/api/x", json={})
    assert r.status_code == 503 and route.call_count == 1


def test_profile_service_headers_are_applied_under_explicit_headers(respx_mock):
    p = Profile(
        name="test",
        services={"example-payment": {"base_url": "http://example-payment",
                                      "headers": {"X-Api-Key": "k1", "X-Env": "profile"}}},
        http={"retries": {"count": 0}},
    )
    route = respx_mock.get("http://example-payment/api/x").respond(200, json={})
    HttpClient(p).get("example-payment", "/api/x", headers={"X-Env": "explicit"})
    sent = route.calls.last.request.headers
    assert sent["x-api-key"] == "k1" and sent["x-env"] == "explicit"


def test_header_precedence_is_case_insensitive(respx_mock):
    p = Profile(
        name="test",
        services={"example-payment": {"base_url": "http://example-payment",
                                      "headers": {"x-api-key": "profile"}}},
        http={"retries": {"count": 0}},
    )
    route = respx_mock.get("http://example-payment/api/x").respond(200, json={})
    HttpClient(p).get("example-payment", "/api/x", headers={"X-Api-Key": "explicit"})
    sent = route.calls.last.request.headers
    assert sent.get_list("x-api-key") == ["explicit"]


def test_merge_headers_later_layers_override_regardless_of_case():
    from apitest.http_client import merge_headers

    merged = merge_headers({"Authorization": "a", "X-Trace": "1"}, {"authorization": "b"}, None)
    assert merged == {"authorization": "b", "x-trace": "1"}


def test_files_only_upload_is_described_in_log(respx_mock):
    respx_mock.post("http://example-payment/api/up").respond(200, json={})
    c = HttpClient(_retry_profile())
    c.post("example-payment", "/api/up", files={"file": ("a.csv", b"1,2")})
    assert c.drain_log()[0]["request_body"] == {"file": "<file a.csv>"}
