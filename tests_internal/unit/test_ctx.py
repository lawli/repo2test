import pytest

from apitest.ctx import Case, Ctx
from apitest.exceptions import MissingExtract


def test_case_carries_id_and_parametric_ids():
    case = Case(id="full-uuid", id_short="abcdef012345", user_id=701234, order_id="V-x")
    assert case.id_short == "abcdef012345"
    assert case.user_id == 701234


def test_ctx_extract_then_attribute_access():
    ctx = Ctx(case=Case(id="x", id_short="y"))
    ctx.set_extract("order_id", 42)
    assert ctx.order_id == 42


def test_ctx_missing_extract_lists_available():
    ctx = Ctx(case=Case(id="x", id_short="y"))
    ctx.set_extract("token", "abc")
    with pytest.raises(MissingExtract) as exc:
        _ = ctx.order_id
    assert "order_id" in str(exc.value) and "token" in str(exc.value)


def test_extract_is_read_only_after_set():
    ctx = Ctx(case=Case(id="x", id_short="y"))
    ctx.set_extract("order_id", 1)
    with pytest.raises(AttributeError):
        ctx.set_extract("order_id", 2)
