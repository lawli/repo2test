import re

import pytest

from apitest.case_id import IdSpec, allocate_case_id, allocate_ids


def test_case_id_short_is_12_hex():
    cid, short = allocate_case_id()
    assert len(short) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", short)
    assert short in cid.replace("-", "")


def test_int64_id_with_prefix():
    _, short = allocate_case_id()
    spec = {"user_id": IdSpec(kind="int64", prefix=70)}
    ids = allocate_ids(spec, case_id_short=short)
    uid = ids["user_id"]
    assert isinstance(uid, int)
    assert str(uid).startswith("70")
    again = allocate_ids(spec, case_id_short=short)
    assert again["user_id"] == uid


def test_string_id_with_prefix():
    _, short = allocate_case_id()
    spec = {"order_id": IdSpec(kind="string", prefix="V-")}
    ids = allocate_ids(spec, case_id_short=short)
    assert ids["order_id"] == f"V-{short}"


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        allocate_ids({"x": IdSpec(kind="bogus")}, case_id_short="abc")
