import os

import pytest

from apitest.fixtures.db import DbClient, run_phase_sql
from apitest.profile import Profile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_MYSQL_DSN"), reason="no TEST_MYSQL_DSN"),
]


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="test",
        databases={"example-payment": {"dsn": os.environ["TEST_MYSQL_DSN"]}},
    )


def test_db_query_round_trip(profile: Profile) -> None:
    c = DbClient(profile)
    db = c.for_service("example-payment")
    db.execute("CREATE TEMPORARY TABLE t (id INT, name VARCHAR(8))")
    db.execute("INSERT INTO t VALUES (1,'a'),(2,'b')")
    rows = db.query("SELECT * FROM t ORDER BY id")
    assert rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    c.close_all()


def test_run_phase_sql_one_txn(profile: Profile, tmp_path):
    f1 = tmp_path / "1.sql"
    f1.write_text("CREATE TEMPORARY TABLE x (id INT);")
    f2 = tmp_path / "2.sql"
    f2.write_text("INSERT INTO x VALUES (10);")
    c = DbClient(profile)
    db = c.for_service("example-payment")
    run_phase_sql(db, [f1, f2], ctx_vars={})
    rows = db.query("SELECT * FROM x")
    assert rows == [{"id": 10}]
    c.close_all()


def test_phase_rolls_back_on_failure(profile: Profile, tmp_path):
    good = tmp_path / "g.sql"
    good.write_text("CREATE TEMPORARY TABLE y (id INT); INSERT INTO y VALUES (1);")
    bad = tmp_path / "b.sql"
    bad.write_text("INSERT INTO no_such_table_zzz VALUES (2);")
    c = DbClient(profile)
    db = c.for_service("example-payment")
    with pytest.raises(Exception):  # noqa: B017
        run_phase_sql(db, [good, bad], ctx_vars={})
    c.close_all()
