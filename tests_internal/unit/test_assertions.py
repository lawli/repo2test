import pytest

from apitest.runner.assertions import AssertionMismatch, assert_response
from apitest.runner.extract import extract_jsonpath


def test_extract_dotted_jsonpath():
    body = {"data": {"order_id": "O-1", "total": 5}}
    assert extract_jsonpath(body, "$.data.order_id") == "O-1"
    assert extract_jsonpath(body, "$.data.total") == 5


def test_status_pass():
    assert_response(status=200, body={}, expected={"status": 200, "json": {}})


def test_status_fail_lists_path():
    with pytest.raises(AssertionMismatch) as exc:
        assert_response(status=500, body={}, expected={"status": 200, "json": {}})
    assert "status" in str(exc.value)


def test_dotted_json_match():
    body = {"code": 0, "data": {"order_id": "O-1"}}
    assert_response(
        status=200,
        body=body,
        expected={"status": 200, "json": {"code": 0, "data.order_id": "O-1"}},
    )


def test_at_type_check():
    body = {"data": {"total": 5}}
    assert_response(
        status=200,
        body=body,
        expected={"status": 200, "json": {"data.total": "@number > 0"}},
    )


def test_at_uuid_pass():
    body = {"id": "550e8400-e29b-41d4-a716-446655440000"}
    assert_response(status=200, body=body, expected={"status": 200, "json": {"id": "@uuid"}})


def test_at_regex():
    body = {"phone": "+15551234567"}
    assert_response(
        status=200,
        body=body,
        expected={"status": 200, "json": {"phone": "@regex /^\\+1\\d{10}$/"}},
    )


def _ok(body, expected):
    assert_response(status=200, body=body, expected={"json": expected})


def _bad(body, expected):
    with pytest.raises(AssertionMismatch):
        assert_response(status=200, body=body, expected={"json": expected})


def test_json_root_and_jsonpath_prefix_match_like_extract():
    # `extract` spells paths `$.data.id`; assertions accept the same spelling.
    _ok(50, {"$": 50})
    _bad(51, {"$": 50})
    _ok({"data": {"id": 7}}, {"$.data.id": 7, "data.id": 7})
    _bad({"data": {"id": 7}}, {"$.data.id": 8})
    _ok([{"id": 1}], {"$.0.id": 1})
    _ok({"a": 1}, {"$.b": "@absent"})


def test_at_absent_and_null():
    _ok({"a": 1}, {"b": "@absent"})
    _bad({"a": 1}, {"a": "@absent"})
    _ok({"a": None}, {"a": "@null"})
    _bad({"a": 0}, {"a": "@null"})


def test_at_len_with_and_without_operator():
    body = {"items": [1, 2, 3], "name": "abc", "obj": {"k": 1}}
    _ok(body, {"items": "@len 3", "name": "@len 3", "obj": "@len 1"})
    _ok(body, {"items": "@len > 2", "name": "@len < 4"})
    _ok(body, {"items": "@len >= 3", "items.0": "@any"})
    _bad(body, {"items": "@len 2"})
    _bad({"n": 5}, {"n": "@len 1"})  # not sized


def test_at_contains_on_string_list_and_dict():
    body = {"msg": "order created", "tags": ["a", "b"], "nums": [1, 2], "obj": {"k": 1}}
    _ok(body, {"msg": "@contains created", "tags": "@contains a", "nums": "@contains 2",
               "obj": "@contains k"})
    _bad(body, {"msg": "@contains deleted"})
    _bad(body, {"tags": "@contains z"})


def test_at_in_uses_json_list():
    _ok({"status": "PAID"}, {"status": '@in ["PAID", "PENDING"]'})
    _ok({"code": 0}, {"code": "@in [0, 1]"})
    _bad({"status": "FAILED"}, {"status": '@in ["PAID", "PENDING"]'})


def test_at_type_accepts_json_and_python_names():
    body = {"s": "x", "n": 1.5, "i": 2, "o": {}, "a": [], "b": True, "z": None}
    _ok(body, {"s": "@type string", "n": "@type number", "i": "@type number",
               "o": "@type object", "a": "@type array", "b": "@type boolean", "z": "@type null"})
    _ok(body, {"s": "@type str", "i": "@type int", "o": "@type dict", "a": "@type list"})
    _bad(body, {"b": "@type number"})  # bool is not a number
    _bad(body, {"s": "@type number"})


def test_headers_and_text_assertions():
    hdrs = {"Content-Type": "text/plain; charset=utf-8", "X-Request-Id": "abc123"}
    assert_response(
        status=200, body={}, headers=hdrs, text="OK: done",
        expected={"headers": {"content-type": "@contains text/plain",
                              "x-request-id": "@regex /^[a-z0-9]+$/"},
                  "text": "@contains done"},
    )
    with pytest.raises(AssertionMismatch, match="header"):
        assert_response(status=200, body={}, headers=hdrs, text="",
                        expected={"headers": {"x-missing": "@any"}})
    with pytest.raises(AssertionMismatch, match="text"):
        assert_response(status=200, body={}, headers=hdrs, text="ERR",
                        expected={"text": "OK"})


def test_malformed_matcher_dsl_is_a_mismatch_not_an_error():
    body = {"n": [1, 2], "obj": {"a": 1}, "s": "x"}
    for expected in ("@len", "@len 3.5", '@in [PAID', "@contains {\"a\": 1}", "@number x"):
        with pytest.raises(AssertionMismatch, match="malformed|expected"):
            assert_response(status=200, body=body,
                            expected={"json": {"obj": expected, "n": expected, "s": expected}})
