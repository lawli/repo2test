import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_MYSQL_DSN"), reason="no TEST_MYSQL_DSN"),
]


def test_sweep_replays_manifest(tmp_path: Path) -> None:
    import pymysql

    u = urlparse(os.environ["TEST_MYSQL_DSN"])
    conn = pymysql.connect(
        host=u.hostname,
        port=u.port or 3306,
        user=u.username or "",
        password=u.password or "",
        database=(u.path or "/").lstrip("/"),
    )
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS sptest_sweep (id BIGINT PRIMARY KEY)")
        cur.execute("INSERT INTO sptest_sweep VALUES (999000001)")
    conn.commit()

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "run_id": "r1",
            "case_id": "c1",
            "service": "example-payment",
            "table": "sptest_sweep",
            "id_value": 999000001,
        })
        + "\n"
    )

    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles/test.yaml").write_text(
        "environment: non-production\n"
        "databases:\n  example-payment: { dsn: '${TEST_MYSQL_DSN}' }\n"
        "services:\n  example-payment: { base_url: 'http://x', auth_mode: jwt }\n"
    )

    p = subprocess.run(
        ["apitest", "sweep", "--manifest", str(manifest), "--profile", "test"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sptest_sweep WHERE id = 999000001")
        row = cur.fetchone()
        n = row[0] if row else 0
    assert n == 0
    conn.close()
