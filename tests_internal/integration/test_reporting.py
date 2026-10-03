import json
import textwrap

import pytest
import yaml


def test_summary_json_emitted(pytester: pytest.Pytester, respx_mock: object) -> None:
    respx_mock.post("http://example-payment/api/x").respond(200, json={"ok": True})  # type: ignore[attr-defined]

    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://example-payment', auth_mode: jwt }\n"
    )
    tests = pytester.mkdir("tests")
    svc = tests / "example-payment"
    svc.mkdir()
    (svc / "case_001.yaml").write_text(
        textwrap.dedent("""
        schema: v1
        mutates: false
        name: case_001
        service: example-payment
        steps:
          - name: do
            request: { method: POST, path: /api/x }
            assert: { status: 200, json: { ok: true } }
    """)
    )

    pytester.makeini(
        "[pytest]\n"
        "apitest_profile = test\n"
        "apitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    rdir = pytester.path / "reports"
    rdir.mkdir()
    pytester.runpytest("-v", f"--apitest-summary-json={rdir / 'summary.json'}").assert_outcomes(
        passed=1
    )

    s = json.loads((rdir / "summary.json").read_text())
    assert s["totals"]["pass"] == 1
    assert any(c["name"] == "case_001" for c in s["cases"])


def test_summary_counts_invalid_case_as_error(pytester: pytest.Pytester) -> None:
    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(
        "services:\n  example-payment: { base_url: 'http://example-payment' }\n"
    )
    svc = pytester.mkdir("tests") / "example-payment"
    svc.mkdir()
    (svc / "case_001_bad.yaml").write_text(
        "schema: v1\nname: case_001_bad\nservice: example-payment\ntags: nope\nsteps: []\n"
    )
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    rdir = pytester.path / "reports"
    rdir.mkdir()
    pytester.runpytest(f"--apitest-summary-json={rdir / 'summary.json'}").assert_outcomes(failed=1)
    s = json.loads((rdir / "summary.json").read_text())
    assert s["totals"] == {
        "pass": 0,
        "fail": 0,
        "error": 1,
        "cleanup_failures": 0,
        "blocked": 0,
        "xfail": 0,
        "xpass": 0,
        "pending_fail": 0,
    }
    assert s["cases"][0]["service"] == "example-payment"


def test_summary_collects_results_from_xdist_workers(pytester: pytest.Pytester) -> None:
    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(
        "services:\n  svc-a: { base_url: 'http://127.0.0.1:9' }\n"
        "  svc-b: { base_url: 'http://127.0.0.1:9' }\n"
        "http: { retries: { count: 0 } }\n"
    )
    tests = pytester.mkdir("tests")
    for svc in ("svc-a", "svc-b"):
        (tests / svc).mkdir()
        (tests / svc / "case_001.yaml").write_text(
            textwrap.dedent(f"""
            schema: v1
            name: case_001
            service: {svc}
            steps:
              - name: do
                request: {{ method: GET, path: /x }}
                assert: {{ status: 200 }}
        """)
        )
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    rdir = pytester.path / "reports"
    rdir.mkdir()
    result = pytester.runpytest_subprocess(
        "-n",
        "2",
        "--dist",
        "loadgroup",
        "-p",
        "no:cacheprovider",
        f"--apitest-summary-json={rdir / 'summary.json'}",
        "tests",
    )
    result.assert_outcomes(failed=2)  # port 9 refuses connections -> EXECUTE error
    s = json.loads((rdir / "summary.json").read_text())
    assert s["totals"]["error"] == 2
    assert sorted(c["service"] for c in s["cases"]) == ["svc-a", "svc-b"]
    assert all("EXECUTE error" in c["error_message"] for c in s["cases"])


def test_strict_defects_and_cleanup_failure_reach_reports(pytester, respx_mock):
    import xml.etree.ElementTree as ET

    respx_mock.get("http://orders/bad").respond(400)
    respx_mock.get("http://orders/good").respond(200)
    respx_mock.delete("http://orders/owned").respond(500)
    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text(
        "environment: non-production\nservices:\n  orders: {base_url: 'http://orders'}\n"
    )
    tests = pytester.mkdir("tests")
    for name, path in [("xfail", "/bad"), ("xpass", "/good"), ("cleanup", "/good")]:
        case = {
            "schema": "v1",
            "name": name,
            "service": "orders",
            "steps": [
                {
                    "name": "check",
                    "request": {"method": "GET", "path": path},
                    "assert": {"status": 200},
                }
            ],
        }
        if name == "cleanup":
            case["data"] = {"isolated": True, "cleanup": "delete owned record"}
            case["teardown"] = {
                "steps": [{"name": "delete", "request": {"method": "DELETE", "path": "/owned"}}]
            }
        else:
            case["known_defects"] = [
                {"ref": "https://issues.invalid/1", "step": "check", "at": "status"}
            ]
        (tests / f"case_{name}.yaml").write_text(yaml.safe_dump(case))
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    summary, junit, html = (
        pytester.path / name for name in ("summary.json", "junit.xml", "report.html")
    )
    result = pytester.runpytest(
        f"--apitest-summary-json={summary}",
        f"--junitxml={junit}",
        f"--html={html}",
    )
    result.assert_outcomes(xfailed=1, failed=2)
    counts = json.loads(summary.read_text())["totals"]
    assert counts["xfail"] == counts["xpass"] == counts["error"] == counts["cleanup_failures"] == 1
    report = ET.parse(junit)
    assert len(report.findall(".//failure")) == 2
    assert len(report.findall(".//skipped")) == 1
    for output in (result.stdout.str(), junit.read_text(), html.read_text()):
        assert "teardown step delete" in output
        assert "delete owned record" in output
        assert "Residual data" in output
        assert "id_short" in output


def test_known_defect_report_keeps_the_http_exchanges(pytester, respx_mock):
    respx_mock.get("http://orders/bad").respond(400, json={"error": "nope-marker"})
    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text("services:\n  orders: {base_url: 'http://orders'}\n")
    tests = pytester.mkdir("tests")
    case = {
        "schema": "v1",
        "name": "xfail",
        "service": "orders",
        "steps": [
            {
                "name": "check",
                "request": {"method": "GET", "path": "/bad"},
                "assert": {"status": 200},
            }
        ],
        "known_defects": [{"ref": "https://issues.invalid/1", "step": "check", "at": "status"}],
    }
    (tests / "case_xfail.yaml").write_text(yaml.safe_dump(case))
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    junit, html = (pytester.path / name for name in ("junit.xml", "report.html"))
    result = pytester.runpytest(f"--junitxml={junit}", f"--html={html}")
    result.assert_outcomes(xfailed=1)
    for output in (junit.read_text(), html.read_text()):
        assert "status: expected 200, got 400" in output
        assert "nope-marker" in output


def test_failure_text_names_the_case_relative_to_the_workspace(pytester, respx_mock):
    import xml.etree.ElementTree as ET

    respx_mock.get("http://orders/bad").respond(400, json={})
    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text("services:\n  orders: {base_url: 'http://orders'}\n")
    tests = pytester.mkdir("tests")
    case = {
        "schema": "v1",
        "name": "fail",
        "service": "orders",
        "steps": [
            {
                "name": "check",
                "request": {"method": "GET", "path": "/bad"},
                "assert": {"status": 200},
            }
        ],
    }
    (tests / "case_fail.yaml").write_text(yaml.safe_dump(case))
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    junit, html = (pytester.path / name for name in ("junit.xml", "report.html"))
    result = pytester.runpytest(f"--junitxml={junit}", f"--html={html}")
    result.assert_outcomes(failed=1)
    failure = ET.parse(junit).find(".//failure").text
    assert failure.startswith("tests/case_fail.yaml::case_fail\nstatus: expected 200, got 400")
    absolute = str(tests / "case_fail.yaml")
    assert absolute not in failure and absolute not in html.read_text()


def test_failed_pending_expectation_is_labelled_and_counted_separately(pytester, respx_mock):
    import xml.etree.ElementTree as ET

    respx_mock.get("http://orders/x").respond(404)
    profiles = pytester.mkdir("profiles")
    (profiles / "test.yaml").write_text("services:\n  orders: {base_url: 'http://orders'}\n")
    tests = pytester.mkdir("tests")
    for expectation in ("pending", "confirmed"):
        case = {
            "schema": "v1",
            "name": expectation,
            "service": "orders",
            "expectation": expectation,
            "steps": [
                {
                    "name": "check",
                    "request": {"method": "GET", "path": "/x"},
                    "assert": {"status": 200},
                }
            ],
        }
        (tests / f"case_{expectation}.yaml").write_text(yaml.safe_dump(case))
    pytester.makeini(
        "[pytest]\napitest_profile = test\napitest_profiles_dir = profiles\n"
        "apitest_tests_root = tests\n"
    )
    summary, junit = (pytester.path / name for name in ("summary.json", "junit.xml"))
    result = pytester.runpytest(f"--apitest-summary-json={summary}", f"--junitxml={junit}")
    result.assert_outcomes(failed=2)
    totals = json.loads(summary.read_text())["totals"]
    assert totals["fail"] == 2 and totals["pending_fail"] == 1
    failures = {
        case.get("name"): case.find("failure").text for case in ET.parse(junit).iter("testcase")
    }
    assert "pending expectation not met" in failures["case_pending"]
    assert "pending expectation not met" not in failures["case_confirmed"]
