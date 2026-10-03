import pytest

from apitest.ctx import Case, Ctx
from apitest.http_client import HttpClient
from apitest.profile import Profile
from apitest.runner.steps import execute_step
from apitest.schema.case_v1 import Step


@pytest.fixture
def http(respx_mock) -> HttpClient:
    p = Profile(
        name="t",
        services={"example-payment": {"base_url": "http://example-payment", "auth_mode": "jwt"}},
    )
    return HttpClient(p)


def test_step_executes_request_and_extracts(http: HttpClient, respx_mock) -> None:
    respx_mock.post("http://example-payment/api/orders").respond(
        200, json={"data": {"order_id": "O-1"}}
    )
    ctx = Ctx(case=Case(id="x", id_short="abc"))
    step = Step.model_validate({
        "name": "create",
        "request": {"method": "POST", "path": "/api/orders"},
        "extract": {"order_id": "$.data.order_id"},
        "assert": {"status": 200, "json": {"data.order_id": "O-1"}},
    })
    execute_step(step, ctx=ctx, http=http, service="example-payment", scope_dir=None)
    assert ctx.order_id == "O-1"


def test_path_interpolation(http: HttpClient, respx_mock) -> None:
    respx_mock.get("http://example-payment/api/orders/O-7/status").respond(
        200, json={"status": "ok"}
    )
    ctx = Ctx(case=Case(id="x", id_short="abc"))
    ctx.set_extract("order_id", "O-7")
    step = Step.model_validate({
        "name": "get_status",
        "request": {"method": "GET", "path": "/api/orders/${ctx.order_id}/status"},
        "assert": {"status": 200},
    })
    execute_step(step, ctx=ctx, http=http, service="example-payment", scope_dir=None)


def test_extracted_int_round_trips_through_body_and_assert(http: HttpClient, respx_mock) -> None:
    import json

    import httpx

    respx_mock.post("http://example-payment/api/orders").respond(200, json={"data": {"id": 42}})
    seen: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": 42}})

    respx_mock.get("http://example-payment/api/orders/42").mock(side_effect=capture)
    ctx = Ctx(case=Case(id="x", id_short="abc", user_id=70123))
    create = Step.model_validate({
        "name": "create",
        "request": {"method": "POST", "path": "/api/orders"},
        "extract": {"oid": "$.data.id"},
    })
    read = Step.model_validate({
        "name": "read",
        "request": {
            "method": "GET",
            "path": "/api/orders/${ctx.oid}",
            "body": {"id": "${ctx.oid}", "uid": "${ctx.case.user_id}", "label": "o-${ctx.oid}"},
        },
        "assert": {"status": 200, "json": {"data.id": "${ctx.oid}"}},
    })
    execute_step(create, ctx=ctx, http=http, service="example-payment", scope_dir=None)
    execute_step(read, ctx=ctx, http=http, service="example-payment", scope_dir=None)
    assert seen["body"] == {"id": 42, "uid": 70123, "label": "o-42"}


def test_status_mismatch_is_reported_before_extract(http: HttpClient, respx_mock) -> None:
    from apitest.runner.assertions import AssertionMismatch

    respx_mock.post("http://example-payment/api/orders").respond(500, json={"error": "boom"})
    ctx = Ctx(case=Case(id="x", id_short="abc"))
    step = Step.model_validate({
        "name": "create",
        "request": {"method": "POST", "path": "/api/orders"},
        "extract": {"oid": "$.data.id"},
        "assert": {"status": 200},
    })
    with pytest.raises(AssertionMismatch) as exc:
        execute_step(step, ctx=ctx, http=http, service="example-payment", scope_dir=None)
    assert "status: expected 200, got 500" in str(exc.value)


def test_missing_extract_path_is_an_assertion_failure_with_context(
    http: HttpClient, respx_mock
) -> None:
    from apitest.runner.assertions import AssertionMismatch

    respx_mock.post("http://example-payment/api/orders").respond(200, json={"data": {}})
    ctx = Ctx(case=Case(id="x", id_short="abc"))
    step = Step.model_validate({
        "name": "create",
        "request": {"method": "POST", "path": "/api/orders"},
        "extract": {"oid": "$.data.id"},
        "assert": {"status": 200},
    })
    with pytest.raises(AssertionMismatch) as exc:
        execute_step(step, ctx=ctx, http=http, service="example-payment", scope_dir=None)
    msg = str(exc.value)
    assert "oid" in msg and "$.data.id" in msg and "200" in msg
    assert exc.value.at == "extract.oid"


def _capture(respx_mock, method, url, status=200, payload=None):
    import httpx

    seen: dict = {}

    def side_effect(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(status, json=payload if payload is not None else {})

    getattr(respx_mock, method)(url).mock(side_effect=side_effect)
    return seen


def _run(step_dict, http, ctx=None, scope_dir=None, base_headers=None):
    ctx = ctx or Ctx(case=Case(id="x", id_short="abc", user_id=70123))
    step = Step.model_validate(step_dict)
    execute_step(step, ctx=ctx, http=http, service="example-payment", scope_dir=scope_dir,
                 base_headers=base_headers or {})
    return ctx


def test_params_are_rendered_and_sent_as_query_string(http: HttpClient, respx_mock) -> None:
    seen = _capture(respx_mock, "get", "http://example-payment/api/items")
    _run({"name": "q", "request": {"method": "GET", "path": "/api/items",
                                   "params": {"limit": 5, "owner": "${ctx.case.user_id}"}}}, http)
    assert seen["request"].url.params["limit"] == "5"
    assert seen["request"].url.params["owner"] == "70123"


def test_form_body_is_urlencoded(http: HttpClient, respx_mock) -> None:
    seen = _capture(respx_mock, "post", "http://example-payment/api/login")
    _run({"name": "f", "request": {"method": "POST", "path": "/api/login",
                                   "form": {"user": "u-${ctx.case.id_short}", "pw": "x"}}}, http)
    req = seen["request"]
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert req.content == b"user=u-abc&pw=x"


def test_raw_body_is_rendered_and_sent_as_bytes(http: HttpClient, respx_mock) -> None:
    seen = _capture(respx_mock, "post", "http://example-payment/api/xml")
    _run({"name": "r", "request": {"method": "POST", "path": "/api/xml",
                                   "headers": {"Content-Type": "application/xml"},
                                   "raw": "<id>${ctx.case.user_id}</id>"}}, http)
    req = seen["request"]
    assert req.content == b"<id>70123</id>"
    assert req.headers["content-type"] == "application/xml"


def test_files_upload_as_multipart(http: HttpClient, respx_mock, tmp_path) -> None:
    (tmp_path / "up.csv").write_text("a,b\n1,2\n")
    seen = _capture(respx_mock, "post", "http://example-payment/api/upload")
    _run({"name": "u", "request": {"method": "POST", "path": "/api/upload",
                                   "files": {"file": "up.csv"}, "form": {"kind": "csv"}}},
         http, scope_dir=tmp_path)
    req = seen["request"]
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'filename="up.csv"' in req.content and b"a,b\n1,2\n" in req.content
    assert b'name="kind"' in req.content


def test_head_and_options_methods_are_allowed() -> None:
    for m in ("HEAD", "OPTIONS"):
        Step.model_validate({"name": "h", "request": {"method": m, "path": "/x"}})


def test_only_one_body_kind_per_request() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Step.model_validate({"name": "b", "request": {"method": "POST", "path": "/x",
                                                       "body": {"a": 1}, "form": {"b": 2}}})


def test_assert_headers_and_text_on_non_json_response(http: HttpClient, respx_mock) -> None:
    import httpx

    respx_mock.get("http://example-payment/api/ping").mock(
        return_value=httpx.Response(200, text="pong", headers={"X-Node": "n1"})
    )
    _run({"name": "p", "request": {"method": "GET", "path": "/api/ping"},
          "assert": {"status": 200, "text": "pong", "headers": {"x-node": "@regex /^n\\d$/"}}},
         http)


def test_case_headers_apply_to_every_step_and_step_headers_win(
    http: HttpClient, respx_mock
) -> None:
    seen = _capture(respx_mock, "get", "http://example-payment/api/me")
    ctx = Ctx(case=Case(id="x", id_short="abc"))
    ctx.set_extract("token", "T1")
    base = {"Authorization": "Bearer ${ctx.token}", "X-Trace": "case"}
    _run({"name": "a", "request": {"method": "GET", "path": "/api/me"}}, http, ctx=ctx,
         base_headers=base)
    assert seen["request"].headers["authorization"] == "Bearer T1"
    assert seen["request"].headers["x-trace"] == "case"
    _run({"name": "b", "request": {"method": "GET", "path": "/api/me",
                                   "headers": {"X-Trace": "step"}}}, http, ctx=ctx,
         base_headers=base)
    assert seen["request"].headers["x-trace"] == "step"
    assert seen["request"].headers["authorization"] == "Bearer T1"


def test_step_header_overrides_case_header_regardless_of_case(
    http: HttpClient, respx_mock
) -> None:
    seen = _capture(respx_mock, "get", "http://example-payment/api/me")
    _run({"name": "b", "request": {"method": "GET", "path": "/api/me",
                                   "headers": {"authorization": "Bearer STEP"}}}, http,
         base_headers={"Authorization": "Bearer CASE"})
    assert seen["request"].headers.get_list("authorization") == ["Bearer STEP"]
