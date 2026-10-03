from pathlib import Path

import pytest


def test_smoke_case_runs_via_pytest(
    monkeypatch: pytest.MonkeyPatch, respx_mock: object
) -> None:
    monkeypatch.setenv("LOCAL_EXAMPLE_DB_USER", "u")
    monkeypatch.setenv("LOCAL_EXAMPLE_DB_PASS", "p")
    monkeypatch.setenv("LOCAL_MQ_USER", "u")
    monkeypatch.setenv("LOCAL_MQ_PASS", "p")
    repo = Path(__file__).resolve().parents[2]
    respx_mock.get("http://localhost:8081/api/health").respond(  # type: ignore[attr-defined]
        200, json={"status": "UP"}
    )
    code = pytest.main([
        "-v",
        str(repo / "tests/example/health/case_001_ping.yaml"),
        "--rootdir",
        str(repo),
    ])
    assert code == 0
