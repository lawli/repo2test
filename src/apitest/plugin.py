"""Pytest plugin: YAML case discovery + lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from apitest.envfile import load_env_file
from apitest.escape import HelperResolver
from apitest.http_client import format_http_log
from apitest.profile import Profile, load_profile
from apitest.reporting.summary import write_summary
from apitest.runner.lifecycle import CaseOutcome, CaseResult, run_case
from apitest.schema.case_v1 import Case as CaseModel
from apitest.selector import is_case_file
from apitest.workspace import Workspace, find_workspace


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini("apitest_profile", "Profile to use", default="local")
    parser.addini("apitest_profiles_dir", "Profiles directory", default="profiles")
    parser.addini("apitest_tests_root", "Tests root (yaml cases)", default="tests")
    parser.addoption("--apitest-summary-json", default=None, help="path to write summary.json")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "apitest_case: yaml-driven case")
    config.pluginmanager.register(_SummaryCollector(), "apitest-summary")
    # Direct `pytest` runs see the same .env as `apitest run`. Only inside a workspace, so a
    # developer's .env never leaks into unrelated suites such as this framework's own tests.
    root = find_workspace(Path(str(config.rootpath)))
    if root:
        load_env_file(root)


def _ini(config: pytest.Config, key: str) -> str:
    return str(config.getini(key))


def _resolve_paths(config: pytest.Config) -> tuple[Path, Path]:
    rootdir = Path(str(config.rootpath))
    if find_workspace(rootdir):
        workspace = Workspace.load(rootdir)
        return workspace.path("profiles"), workspace.path("tests")
    return (
        rootdir / _ini(config, "apitest_profiles_dir"),
        rootdir / _ini(config, "apitest_tests_root"),
    )


class ApitestYamlFile(pytest.File):
    def collect(self) -> Any:
        """A file that fails to parse/validate still yields one item, which fails
        at run time — so one bad YAML never aborts collection of the suite."""
        data: Any = None
        try:
            data = yaml.safe_load(self.path.read_text()) or {}
            case: CaseModel | None = CaseModel.model_validate(data)
            load_error: str | None = None
        except Exception as e:  # noqa: BLE001 - any load failure becomes a failing item
            case, load_error = None, f"{self.path}: {e}"
        service = _service_of(case, data, self.path, _resolve_paths(self.config)[1])
        yield ApitestYamlItem.from_parent(
            self, name=self.path.stem, case=case, service=service, load_error=load_error
        )


def _service_of(case: CaseModel | None, data: Any, path: Path, tests_root: Path) -> str:
    if case is not None:
        return case.service
    if isinstance(data, dict) and isinstance(data.get("service"), str):
        return str(data["service"])
    try:
        return path.resolve().relative_to(tests_root.resolve()).parts[0]
    except (ValueError, IndexError):
        return path.stem


class ApitestYamlItem(pytest.Item):
    def __init__(
        self,
        *,
        name: str,
        parent: Any,
        case: CaseModel | None,
        service: str,
        load_error: str | None = None,
    ) -> None:
        super().__init__(name, parent)
        self._case = case
        self._service = service
        self._load_error = load_error
        self.add_marker(pytest.mark.apitest_case)
        # Cases of one service serialise on one xdist worker unless the case
        # declares parallel_safe: true (then loadgroup schedules it freely).
        if case is None or case.parallel_safe is not True:
            # A cross-service case must not race a single-service case on a
            # different worker. Non-parallel-safe cases share one group.
            self.add_marker(pytest.mark.xdist_group(name="apitest-serial"))

    def _record(self, result: CaseResult) -> None:
        # user_properties travel with the report, so results reach the
        # controller under xdist as well as in-process.
        self.user_properties.append(
            (
                "apitest",
                {
                    "node_id": self.nodeid,
                    "name": self.name,
                    "service": self._service,
                    "outcome": result.outcome.value,
                    "error_message": result.error_message,
                    "teardown_errors": result.teardown_errors,
                    "timings": result.timings,
                    "business_outcome": result.business_outcome,
                    "failed_step": result.failed_step,
                    "failed_at": result.failed_at,
                    "residual_data": result.residual_data,
                    "cleanup_guidance": result.cleanup_guidance,
                    "profile": _ini(self.config, "apitest_profile"),
                    "expectation": self._case.expectation if self._case else "pending",
                    "covers": [c.model_dump() for c in self._case.covers] if self._case else [],
                    "source_commit": self._case.source_commit if self._case else None,
                },
            )
        )

    def runtest(self) -> None:
        if self._case is None:
            msg = self._load_error or f"{self.path}: could not load case"
            self._record(CaseResult(outcome=CaseOutcome.ERROR, error_message=msg))
            pytest.fail(msg, pytrace=False)
        config = self.config
        profiles_dir, tests_root = _resolve_paths(config)
        try:
            profile: Profile = load_profile(
                _ini(config, "apitest_profile"), profiles_dir=profiles_dir, resolve=False
            )
            resolver = HelperResolver(tests_root=tests_root)
            result = run_case(
                self._case,
                profile=profile,
                scope_dir=self.path.parent,
                helper_resolver=resolver,
                case_yaml_path=self.path,
            )
        except Exception as exc:
            from apitest.taint import redact

            result = CaseResult(outcome=CaseOutcome.ERROR, error_message=redact(str(exc)))
        self._record(result)
        if result.outcome is CaseOutcome.PASS:
            return
        exchanges = (
            "\n--- http exchanges ---\n" + format_http_log(result.http_log)
            if result.http_log
            else ""
        )
        if result.outcome is CaseOutcome.XFAIL:
            pytest.xfail((result.error_message or "known defect") + exchanges)
        msg = result.error_message or f"case {result.outcome.value.lower()}"
        if result.outcome is CaseOutcome.FAIL and self._case.expectation == "pending":
            msg = "pending expectation not met: " + msg
        if result.teardown_errors:
            msg += "\n--- cleanup failures ---\n" + "\n".join(result.teardown_errors)
            msg += "\nBusiness outcome: " + str(result.business_outcome)
            msg += "\nResidual data: " + json.dumps(result.residual_data, ensure_ascii=False)
            msg += "\nRecovery: " + (result.cleanup_guidance or "Inspect the residual data above.")
        msg += exchanges
        if result.outcome is CaseOutcome.FAIL:
            raise AssertionError(msg)
        pytest.fail(msg, pytrace=False)

    def repr_failure(self, excinfo: Any, style: Any = None) -> str:
        return f"{self.nodeid}\n{excinfo.value}"


def pytest_collect_file(parent: Any, file_path: Path) -> pytest.File | None:
    if not is_case_file(file_path):
        return None
    tests_root = _resolve_paths(parent.config)[1]
    try:
        file_path.relative_to(tests_root)
    except ValueError:
        return None
    result: pytest.File = ApitestYamlFile.from_parent(parent, path=file_path)
    return result


class _SummaryCollector:
    """Gathers per-case results from run reports and writes summary.json."""

    def __init__(self) -> None:
        self._cases: dict[str, dict[str, Any]] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when != "call":
            return
        for name, value in report.user_properties:
            if name == "apitest" and isinstance(value, dict):
                self._cases[report.nodeid] = value

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        out_path = session.config.getoption("--apitest-summary-json")
        if not out_path or hasattr(session.config, "workerinput"):  # workers don't write
            return
        metadata = {}
        if find_workspace(session.config.rootpath):
            metadata = Workspace.load(session.config.rootpath).identity()
        metadata["profile"] = _ini(session.config, "apitest_profile")
        metadata["collected_cases"] = session.testscollected
        metadata["exit_code"] = int(exitstatus)
        write_summary(out_path, list(self._cases.values()), metadata=metadata)
