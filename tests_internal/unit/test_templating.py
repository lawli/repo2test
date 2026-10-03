import json
from pathlib import Path

import pytest

from apitest.templating import render_string, render_template


def test_render_string_with_ctx_var():
    out = render_string("hello ${name}", {"name": "alice"})
    assert out == "hello alice"


def test_render_template_file_with_globals(tmp_path: Path):
    (tmp_path / "x.j2").write_text('{"id": "{{ uuid4() }}", "u": {{ user_id }}}')
    out = render_template(tmp_path / "x.j2", {"user_id": 1001})
    parsed = json.loads(out)
    assert parsed["u"] == 1001
    assert len(parsed["id"]) == 36


def test_dollar_braces_resolve_from_ctx():
    out = render_string("user=${ctx.case.user_id}", {"ctx": {"case": {"user_id": 70}}})
    assert out == "user=70"


def test_render_value_single_expression_keeps_native_type():
    from apitest.templating import render_value

    assert render_value("${ctx.oid}", {"ctx": {"oid": 42}}) == 42
    assert render_value("${ctx.flag}", {"ctx": {"flag": True}}) is True
    assert render_value("${ctx.payload}", {"ctx": {"payload": {"a": 1}}}) == {"a": 1}


def test_render_value_keeps_string_ids_as_strings():
    from apitest.templating import render_value

    out = render_value("${ctx.profile.test_data.user_no}",
                       {"ctx": {"profile": {"test_data": {"user_no": "0000000000000"}}}})
    assert out == "0000000000000"


def test_render_value_mixed_text_renders_to_string():
    from apitest.templating import render_value

    assert render_value("order-${ctx.oid}", {"ctx": {"oid": 42}}) == "order-42"
    assert render_value("plain", {}) == "plain"


def test_render_value_undefined_raises():
    from jinja2 import UndefinedError

    from apitest.templating import render_value

    with pytest.raises(UndefinedError):
        render_value("${ctx.nope}", {"ctx": {}})
