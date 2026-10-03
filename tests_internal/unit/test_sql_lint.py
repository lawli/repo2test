from pathlib import Path

from apitest.sql_lint import lint_sql


def test_literal_id_in_insert_flagged():
    issues = lint_sql("INSERT INTO t_user (id, name) VALUES (1001, 'a');", path=Path("x.sql"))
    assert any("literal" in i.kind for i in issues)


def test_ctx_id_passes():
    issues = lint_sql(
        "INSERT INTO t_user (id, name) VALUES (${ctx.case.user_id}, 'a');",
        path=Path("x.sql"),
    )
    assert issues == []


def test_env_var_in_sql_rejected():
    issues = lint_sql("INSERT INTO t (token) VALUES ('${DB_PASS}');", path=Path("x.sql"))
    assert any("env" in i.kind.lower() for i in issues)


def test_blanket_delete_without_where_rejected():
    issues = lint_sql("DELETE FROM t_order;", path=Path("x.sql"))
    assert any("blanket-delete" in i.kind for i in issues)


def test_allow_marker_suppresses():
    sql = "INSERT INTO t_status (id, name) VALUES (1, 'PENDING'); -- allow-literal-id status enum\n"
    issues = lint_sql(sql, path=Path("x.sql"))
    assert issues == []
