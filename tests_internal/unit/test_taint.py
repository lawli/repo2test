from apitest.taint import TaintedStr, is_tainted, redact


def test_tainted_str_is_str_subclass():
    t = TaintedStr("secret123", source="DB_PASS")
    assert isinstance(t, str)
    assert str.__eq__(t, "secret123")
    assert is_tainted(t)


def test_redact_replaces_tainted_in_text():
    t = TaintedStr("hunter2", source="DB_PASS")
    out = redact(f"connection ok with pw={t}")
    assert "hunter2" not in out
    assert "***" in out


def test_redact_leaves_untainted_alone():
    out = redact("hello world")
    assert out == "hello world"


def test_redact_handles_dict():
    t = TaintedStr("xyz", source="MQ_PASS")
    d = {"url": f"amqp://user:{t}@broker/", "ok": True}
    r = redact(d)
    assert "xyz" not in r["url"]
    assert "***" in r["url"]
    assert r["ok"] is True


def test_redact_propagates_through_concat():
    t = TaintedStr("abc", source="X")
    combined = "prefix-" + t
    assert "abc" not in redact(str(combined))
