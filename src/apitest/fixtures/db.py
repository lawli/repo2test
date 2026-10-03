"""DB engine for apitest."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from apitest.fixtures.readonly_sql import check_verification_sql
from apitest.profile import Profile
from apitest.templating import render_string


def _split_statements(sql: str) -> list[str]:
    """Split a SQL blob into statements, ignoring `;` inside comments + strings.

    Handles `--` line comments, `/* ... */` block comments, and single-quoted
    string literals (with `''` escape). Returns trimmed non-empty statements.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            buf.append(sql[i:j])
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(sql[i:j])
            i = j
            continue
        if ch == "'":
            buf.append(ch)
            i += 1
            while i < n:
                if sql[i] == "'" and (i + 1 >= n or sql[i + 1] != "'"):
                    buf.append("'")
                    i += 1
                    break
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                buf.append(sql[i])
                i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


class DbHandle:
    def __init__(self, conn: Connection, *, readonly: bool = False) -> None:
        self._conn = conn
        self._readonly = readonly

    def query(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        if self._readonly:
            check_verification_sql(sql)
            try:
                with self._conn.cursor(DictCursor) as cur:
                    cur.execute("START TRANSACTION READ ONLY")
                    cur.execute(sql, params or None)
                    return list(cur.fetchall())
            finally:
                # Each poll gets a fresh snapshot. This connection never shares
                # pending writes or temporary tables with setup/helpers.
                self._conn.rollback()
        with self._conn.cursor(DictCursor) as cur:
            # pymysql applies `query % args` whenever args is not None; pass
            # None when there are no params so literal `%` (LIKE '%x%',
            # DATE_FORMAT) survives.
            cur.execute(sql, params or None)
            return list(cur.fetchall())

    def execute(self, sql: str, *params: Any) -> int:
        if self._readonly:
            raise ValueError("verification handle is read-only")
        with self._conn.cursor() as cur:
            cur.execute(sql, params or None)
            return cur.rowcount

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> int:
        if self._readonly:
            raise ValueError("verification handle is read-only")
        with self._conn.cursor() as cur:
            cur.executemany(sql, rows)
            return cur.rowcount

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    @property
    def raw(self) -> Connection:
        return self._conn


class DbClient:
    """One pool per service; lazy connect."""

    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._conns: dict[str, Connection] = {}
        self._verification_conns: dict[str, Connection] = {}

    def _connect(self, service: str) -> Connection:
        dsn = self._profile.databases[service]["dsn"]
        u = urlparse(str(dsn))
        return pymysql.connect(
            host=u.hostname,
            port=u.port or 3306,
            user=u.username or "",
            password=u.password or "",
            database=(u.path or "/").lstrip("/"),
            autocommit=False,
            charset="utf8mb4",
        )

    def for_service(self, service: str) -> DbHandle:
        if service not in self._conns:
            self._conns[service] = self._connect(service)
        return DbHandle(self._conns[service])

    def for_verification(self, service: str) -> DbHandle:
        if service not in self._verification_conns:
            self._verification_conns[service] = self._connect(service)
        return DbHandle(self._verification_conns[service], readonly=True)

    def close_all(self) -> None:
        failures = []
        for c in [*self._conns.values(), *self._verification_conns.values()]:
            try:
                c.close()
            except Exception as exc:
                failures.append(str(exc))
        self._conns.clear()
        self._verification_conns.clear()
        if failures:
            raise RuntimeError("; ".join(failures))


def run_phase_sql(handle: DbHandle, files: list[Path], *, ctx_vars: dict[str, Any]) -> None:
    """Run all SQL files for a phase inside a single transaction."""
    with handle.transaction():
        for f in files:
            text = render_string(Path(f).read_text(), ctx_vars)
            for stmt in _split_statements(text):
                handle.execute(stmt)
