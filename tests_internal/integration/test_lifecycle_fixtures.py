import os
from pathlib import Path

import pytest

from apitest.profile import Profile
from apitest.runner.lifecycle import CaseOutcome, run_case
from apitest.schema.case_v1 import Case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_MYSQL_DSN"), reason="no TEST_MYSQL_DSN"),
]


def test_setup_runs_sql_then_teardown_runs_cleanup(tmp_path: Path, respx_mock) -> None:
    respx_mock.get("http://example-payment/api/ping").respond(200, json={"ok": True})

    fixtures = tmp_path / "fixtures/db"
    fixtures.mkdir(parents=True)
    (fixtures / "setup.sql").write_text(
        "CREATE TABLE IF NOT EXISTS sptest_user (id BIGINT PRIMARY KEY);"
        "INSERT INTO sptest_user (id) VALUES (${ctx.case.user_id});"
    )
    (fixtures / "cleanup.sql").write_text("DELETE FROM sptest_user WHERE id = ${ctx.case.user_id};")

    case = Case.model_validate(
        {
            "schema": "v1",
            "data": {"isolated": True, "cleanup": "Run cleanup.sql for the allocated user_id."},
            "name": "fix",
            "service": "example-payment",
            "ids": {"user_id": {"kind": "int64", "prefix": 70}},
            "setup": {"db": ["fixtures/db/setup.sql"]},
            "steps": [
                {
                    "name": "p",
                    "request": {"method": "GET", "path": "/api/ping"},
                    "assert": {"status": 200},
                }
            ],
            "verify": {
                "db": [
                    {
                        "sql": "SELECT id FROM sptest_user WHERE id = ${ctx.case.user_id}",
                        "expect_count": 1,
                    }
                ]
            },
            "teardown": {"db": ["fixtures/db/cleanup.sql"]},
        }
    )

    profile = Profile(
        name="t",
        environment="non-production",
        services={"example-payment": {"base_url": "http://example-payment", "auth_mode": "jwt"}},
        databases={"example-payment": {"dsn": os.environ["TEST_MYSQL_DSN"]}},
    )
    result = run_case(case, profile=profile, scope_dir=tmp_path)
    assert result.outcome == CaseOutcome.PASS
    assert result.teardown_ran is True
