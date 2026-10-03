from unittest.mock import MagicMock

import pytest

from apitest.fixtures.db import DbClient, DbHandle
from apitest.fixtures.readonly_sql import check_verification_sql
from apitest.profile import Profile


@pytest.mark.parametrize(
    "sql",
    [
        "TRUNCATE TABLE audit_probe",
        "DELETE FROM t",
        "UPDATE t SET id=1",
        "/* innocent */ DROP TABLE t",
        "-- comment\nINSERT INTO t VALUES (1)",
        "SELECT 1; DELETE FROM t",
        "/*!50000 DELETE FROM t */ SELECT 1",
        "/*M! DELETE FROM t */ SELECT 1",
        "SELECT 1 /*! INTO OUTFILE '/tmp/x' */",
        "SELECT * INTO OUTFILE '/tmp/x' FROM t",
        "SELECT * FROM t FOR UPDATE",
        "SELECT * FROM t LOCK IN SHARE MODE",
        "SELECT dangerous_function()",
        "SELECT db.count()",
        "SELECT `count`()",
        "SELECT GET_LOCK('x', 10)",
        "SELECT @x := 1",
        "SELECT 'ambiguous\\' ; DELETE FROM t -- '",
        "WITH t AS (SELECT 1) DELETE FROM orders",
        "SELECT 1 /* unclosed",
        "SELECT SUBSTRING(name, 1, 2) FROM t FOR UPDATE",
        "SELECT SUBSTRING(name FROM 1 FOR 2) FROM t FOR UPDATE",
        "SELECT 'it\\'s' ; DELETE FROM t -- '",
        "SELECT SLEEP(5)",
    ],
)
def test_rejects_unsupported_or_mutating_sql_before_opening_a_cursor(sql):
    conn = MagicMock()
    with pytest.raises(ValueError, match="verify.db.sql"):
        DbHandle(conn, readonly=True).query(sql)
    conn.cursor.assert_not_called()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "-- read\nSELECT id FROM orders WHERE id = 12; # trailing comment",
        "SELECT COUNT(*) AS total FROM t WHERE name LIKE 'a%'",
        "SELECT GROUP_CONCAT(DISTINCT name ORDER BY name SEPARATOR ',') FROM t",
        "SELECT 'DELETE; INTO OUTFILE', `update` FROM t",
        "SELECT DATE_FORMAT(created, '%Y'), REPLACE(name, 'a', 'b') FROM t",
        "SELECT id FROM t WHERE id IN (SELECT id FROM other) AND (id > 0)",
        "SELECT YEAR(created), UNIX_TIMESTAMP(created), BIN_TO_UUID(id) FROM t",
        "SELECT SUBSTRING(name FROM 1 FOR 3) FROM t",
        "SELECT id FROM t WHERE name LIKE 'a\\_b%' AND note = 'tab\\tend\\\\'",
    ],
)
def test_readonly_queries_have_fresh_snapshots_and_always_rollback(sql):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"id": 12}]
    handle = DbHandle(conn, readonly=True)
    assert handle.query(sql) == [{"id": 12}]
    assert [c.args for c in cursor.execute.call_args_list] == [
        ("START TRANSACTION READ ONLY",),
        (sql, None),
    ]
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    cursor.execute.side_effect = RuntimeError("server refused read-only write")
    with pytest.raises(RuntimeError):
        handle.query(sql)
    assert conn.rollback.call_count == 2


def test_verification_connection_does_not_commit_pending_setup_writes(monkeypatch):
    connections = [MagicMock(), MagicMock()]
    connect = MagicMock(side_effect=connections)
    monkeypatch.setattr("apitest.fixtures.db.pymysql.connect", connect)
    client = DbClient(Profile(name="local", databases={"shop": {"dsn": "mysql://localhost/db"}}))
    setup = client.for_service("shop")
    setup.execute("INSERT INTO t VALUES (1)")
    verify = client.for_verification("shop")
    verify.query("SELECT 1")
    assert setup.raw is connections[0] and verify.raw is connections[1]
    connections[0].commit.assert_not_called()
    connections[0].rollback.assert_not_called()
    client.close_all()
    for conn in connections:
        conn.close.assert_called_once()


def test_static_template_check_does_not_admit_arbitrary_sql_fragments():
    check_verification_sql("SELECT id FROM t WHERE id = ${ctx.case.id}", template=True)
    with pytest.raises(ValueError):
        check_verification_sql("${ctx.profile.test_data.query}", template=True)
