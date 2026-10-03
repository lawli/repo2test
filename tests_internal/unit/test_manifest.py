from pathlib import Path

from apitest.manifest import ManifestWriter, read_manifest


def test_write_and_read(tmp_path: Path) -> None:
    p = tmp_path / "manifest.jsonl"
    w = ManifestWriter(p, run_id="run-1")
    w.append(case_id="c1", service="example-payment", table="t_user", id_value=70123)
    w.append(case_id="c1", service="example-payment", table="t_order", id_value="O-abc")
    w.close()

    rows = read_manifest(p)
    assert len(rows) == 2
    assert rows[0]["case_id"] == "c1"
    assert rows[0]["table"] == "t_user"
    assert rows[1]["id_value"] == "O-abc"
    assert all(r["run_id"] == "run-1" for r in rows)
