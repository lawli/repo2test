"""DbHandle param passing vs pymysql %-formatting semantics.

pymysql applies `query % args` whenever args is not None — so passing an
empty tuple instead of None breaks any SQL containing a literal `%`
(LIKE '%x%', DATE_FORMAT(..., '%Y')). The fakes below mirror that contract.
"""

from typing import Any

from apitest.fixtures.db import DbHandle


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: str | None = None
        self.rowcount = len(rows)

    def execute(self, query: str, args: Any = None) -> None:
        if args is not None:  # mirrors pymysql Cursor.mogrify
            query = query % args
        self.executed = query

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


class FakeConn:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.last_cursor: FakeCursor | None = None

    def cursor(self, *args: Any, **kwargs: Any) -> FakeCursor:
        self.last_cursor = FakeCursor(self.rows)
        return self.last_cursor


def test_query_without_params_keeps_percent_literals() -> None:
    conn = FakeConn(rows=[{"id": 1}])
    handle = DbHandle(conn)  # type: ignore[arg-type]
    rows = handle.query("SELECT * FROM t WHERE name LIKE 'a%'")
    assert rows == [{"id": 1}]
    assert conn.last_cursor is not None
    assert conn.last_cursor.executed == "SELECT * FROM t WHERE name LIKE 'a%'"


def test_execute_without_params_keeps_percent_literals() -> None:
    conn = FakeConn()
    handle = DbHandle(conn)  # type: ignore[arg-type]
    handle.execute("DELETE FROM t WHERE name LIKE 'tmp_%'")
    assert conn.last_cursor is not None
    assert conn.last_cursor.executed == "DELETE FROM t WHERE name LIKE 'tmp_%'"


def test_query_with_params_still_interpolates() -> None:
    conn = FakeConn(rows=[])
    handle = DbHandle(conn)  # type: ignore[arg-type]
    handle.query("SELECT * FROM t WHERE id = %s", 7)
    assert conn.last_cursor is not None
    assert conn.last_cursor.executed == "SELECT * FROM t WHERE id = 7"
