"""Exercise the shipped skills through a real host in disposable sibling repos.

By default this exercises the user-level entry and a cold runner. Existing user entries
must match the supplied wheel and are never overwritten. A newly installed entry is
removed after the probe. Artifacts record the exact scenario and permissions granted.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

import yaml


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout


def service_url_is_missing(workspace):
    # Use the runner's dotenv and inheritance rules, but resolve only the URL:
    # an unrelated missing credential must not hide an invented service URL.
    # A subprocess keeps local dotenv values out of the probe's environment.
    check = """
import json
from apitest.cli import _load_dotenv
from apitest.profile import ProfileError, _substitute_env, load_profile
from apitest.workspace import Workspace
_load_dotenv()
profile = load_profile("local", Workspace.load().path("profiles"), resolve=False)
try:
    url = _substitute_env(profile.services.get("shop", {}).get("base_url"))
except ProfileError:
    url = None
print(json.dumps(not bool(url and str(url).strip())))
"""
    return json.loads(run([sys.executable, "-c", check], workspace))


def final_response(host, stdout):
    if host == "codex":
        return stdout.strip()
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event.get("result", "")
    return ""


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".git" not in path.parts
    }


def business_snapshot(root):
    return {
        "files": snapshot(root),
        "head": run(["git", "rev-parse", "HEAD"], root),
        "index": run(["git", "ls-files", "--stage"], root),
        "status": run(["git", "status", "--porcelain"], root),
    }


def recording_handler(calls):
    class Handler(BaseHTTPRequestHandler):
        def parse_request(self):
            parsed = super().parse_request()
            if parsed:
                calls.append({"method": self.command, "path": self.path})
            return parsed

        def do_GET(self):
            self.send_response(500)
            self.end_headers()

        do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_GET

        def log_message(self, *unused):
            pass

    return Handler


def fixture_oracle(workspace, scope):
    """Check this fixture's documented boundaries without executing generated code."""
    from apitest.runner.assertions import AssertionMismatch, assert_response
    from apitest.schema.case_v1 import Case

    required = {
        "interior",
        "minimum",
        "maximum",
        "below-minimum",
        "above-maximum",
        "missing",
        "non-integer",
    }
    if scope == "whole":
        required.add("health")
    covered, errors = set(), []

    def accepts(expected, status, body, text):
        try:
            assert_response(status=status, body=body, text=text, expected=expected)
        except (AssertionMismatch, ValueError, TypeError):
            return False
        return True

    for path in (workspace / "tests").rglob("case_*.yaml"):
        try:
            case = Case.model_validate(yaml.safe_load(path.read_text()))
        except Exception as exc:
            errors.append(f"{path.name}: invalid case: {exc}")
            continue
        if case.expectation != "confirmed":
            continue
        for step in case.steps:
            request = step.request
            url = urlsplit(request.path)
            if request.method != "GET" or (step.service or case.service) != "shop":
                continue
            expected = step.assert_.model_dump(by_alias=True)
            expected.pop("headers", None)  # The fixture contract does not prescribe headers.
            category, status, body = None, 400, None
            if url.path == "/api/health":
                category, status, body = "health", 200, "UP"
            elif url.path == "/api/refunds/quote":
                params = {**parse_qs(url.query, keep_blank_values=True), **request.params}
                amount = params.get("amount")
                if isinstance(amount, list):
                    if len(amount) != 1:
                        continue  # Duplicate-parameter behavior is not in this contract.
                    amount = amount[0]
                if "amount" not in params:
                    category = "missing"
                elif "${" in str(amount) or "{{" in str(amount):
                    continue  # Dynamic cases need a separate oracle; never count them as proof.
                elif re.fullmatch(r"[+-]?\d+", str(amount)):
                    number = int(amount)
                    if 1 <= number <= 100:
                        category = {1: "minimum", 100: "maximum"}.get(number, "interior")
                        status, body = 200, number
                    elif number in (0, 101):
                        category = "below-minimum" if number == 0 else "above-maximum"
                else:
                    category = "non-integer"
            if category is None:
                continue
            text = str(body) if body is not None else ""
            if not accepts(expected, status, body, text):
                errors.append(f"{path.name}/{step.name}: assertion contradicts {category} contract")
                continue
            wrong_body = (
                "DOWN" if category == "health" else (body + 1 if body is not None else None)
            )
            checks_status = not accepts(expected, 503, body, text)
            checks_body = status != 200 or not accepts(
                expected, status, wrong_body, str(wrong_body)
            )
            if checks_status and checks_body:
                covered.add(category)
    errors.extend(f"missing contract assertion: {key}" for key in sorted(required - covered))
    return errors


@contextlib.contextmanager
def installed_entry(host, workspace, business, installation, *, user_root=None):
    folder = ".agents" if host == "codex" else ".claude"
    parent = (Path(user_root) if user_root else Path.home()) if installation == "user" else business
    skills = parent / folder / "skills"
    destination = skills / "repo2test"
    expected = snapshot(workspace / "tools/entry/repo2test")
    existed = destination.exists()
    if destination.is_symlink() or (existed and snapshot(destination) != expected):
        raise RuntimeError(
            f"Existing {destination} differs from this wheel; update it explicitly before probing"
        )
    if not existed:
        args = [sys.executable, "tools/install_entry.py", "--host", host]
        # Normal user-level installation uses the installer's real default path.
        if installation == "project" or user_root is not None:
            args += ["--directory", str(skills)]
        run(args, workspace)
    try:
        yield destination
    finally:
        if not existed:
            if snapshot(destination) == expected:
                shutil.rmtree(destination)
            else:
                print(
                    f"Probe entry changed during the run; retained for inspection: {destination}",
                    file=sys.stderr,
                )


def authoring_prompt(mode, scope, scenario, start="business"):
    subject = (
        "为这个 repo 创建 test cases，覆盖所有 API 的 happy path 和 edge cases"
        if scope == "whole"
        else "我们当前只关注退款报价功能，用 repo2test 为它创建 happy path 和 edge cases"
    )
    if scenario == "resume":
        subject += (
            "。上次任务已中断，请从现有 coverage 继续未覆盖场景，保留已有用例 ID、内容及人工注释"
        )
    configuration = (
        "尚未配置 service URL，请引导我提供缺少的配置，同时完成可以独立进行的源码分析"
        if scenario == "missing-url"
        else "local service shop 的 URL 已配置"
    )
    # A workspace start names neither repository: the entry must read repo2test.toml.
    location = (
        "当前目录就是业务仓库的测试仓库"
        if start == "workspace"
        else "测试仓库已经初始化在同级 shop-e2e"
    )
    return (
        subject + f"。{location}，{configuration}，reference 是已部署版本。"
        "遇到权限或环境阻塞请报告具体修复步骤。"
    )


# Transcript fallback for artifacts recorded without timestamps. Deliberately broad so a
# read taken for a write can only fail the check. Writes that no command text reveals,
# such as a generator module imported by another script, can still escape it.
TREE_PATH = re.compile(r"\b(tests|coverage)\b")
WRITE_OP = re.compile(
    r">|\btee\b|\bsed\s+-i|\b(cp|mv|install|dd|rsync|ln|touch|mkdir|patch)\b"
    r"|write_text|write_bytes|\bopen\(|\.dump\(|\.write\("
)
TREE_CD = re.compile(r"\bcd\s+['\"]?\S*\b(tests|coverage)\b")
HARMLESS_REDIRECT = re.compile(r"\d?>&\d|\d?>\s*/dev/null")
INTERPRETER = re.compile(
    r"^(\S*/)?(uv\s+run\s+(-\S+\s+)*)?(\S*/)?(python3?(\.\d+)?|bash|sh|zsh|node|ruby|perl)\b"
    r"|^\./"
)
SCRIPT = re.compile(r"[\w./-]+\.(py|sh|js|rb|pl)\b")
DOCTOR = re.compile(r"^(\S*/)?(uv\s+run\b.*?\s)?(\S*/)?apitest\b.*?\bworkspace\s+doctor\b")
DOCTOR_PASSED = re.compile(r'"problems":\s*\[\s*\]')
LOCATOR = re.compile(r"^(\S+\s+)?\S*locate\.py['\"]?\s+--start\b")
COMMAND_STATUS = re.compile(r"^ (succeeded|exited|failed|declined)\b")
CODEX_MARKERS = {"exec", "codex", "apply patch", "thinking", "user"}


class Event(NamedTuple):
    kind: str  # "command" or "write"
    value: str  # command text or written path
    output: str
    started: float | None  # arrival time of the call, when timestamps were recorded
    finished: float | None  # arrival time of its result


def simple_commands(command):
    """The simple commands of a shell command line, without a `bash -lc` wrapper."""
    command = re.sub(r"^/bin/(ba|z)?sh -lc ", "", command)
    return [part.strip().lstrip("'\"(") for part in re.split(r"\n|;|&&|\|\|?", command)]


def _tool_output(content):
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
    return str(content or "")


def tree_state(roots):
    """(mtime_ns, size) of every file and directory under `roots`, keyed by path."""
    state = {}
    for root in roots:
        for path in [root, *root.rglob("*")] if root.exists() else []:
            with contextlib.suppress(OSError):
                info = path.lstat()
                state[path] = (info.st_mtime_ns, info.st_size)
    return state


def first_change(before, after):
    """Earliest time a difference between two tree states can have happened: the current
    modification time of a new or changed entry, or of the parent of a removed one."""
    times = []
    for path in before.keys() | after.keys():
        if before.get(path) == after.get(path):
            continue
        target = path if path in after else path.parent
        with contextlib.suppress(OSError):
            times.append(target.lstat().st_mtime)
    return min(times, default=None)


def run_host(command, cwd, env, timeout, watch=()):
    """Run the host, timestamping every output line as it arrives.

    Files under `watch` are polled while the host runs; the result includes the earliest
    time a change there can have happened (None if nothing changed). A later rewrite of
    the same file cannot hide that first change."""
    baseline = tree_state(watch)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    timeline, lock, changed = [], threading.Lock(), []

    def pump(stream, name):
        for line in stream:
            with lock:
                timeline.append((time.time(), name, line.rstrip("\n")))

    def poll():
        while not changed:
            detected = time.time()
            moment = first_change(baseline, tree_state(watch))
            if moment is not None:
                changed.append(min(moment, detected))
            elif process.poll() is not None:
                return
            time.sleep(0.1)

    threads = [
        threading.Thread(target=pump, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, "stderr"), daemon=True),
        threading.Thread(target=poll, daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        code = 124
    finally:
        # The host runs in its own session, so Ctrl-C reaches only the probe. On any exit,
        # including an interrupt, kill the host's process group: wrapper hosts leave
        # children holding the pipes, and an unsupervised host must not keep running.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    for thread in threads:
        thread.join()
    return code, timeline, (changed[0] if changed else None)


def session_events(host, log, session_dir):
    """Ordered events of a transcript given as text or as (time, stream, line) entries."""
    if isinstance(log, str):
        entries = [(None, line) for line in log.splitlines()]
    else:
        entries = [(moment, line) for moment, _, line in log]
    events = []
    if host == "claude":
        results, calls = {}, []
        for moment, line in entries:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            # Some events, such as `permission_denied`, carry `message` as plain text.
            inner = message.get("message") if isinstance(message, dict) else None
            content = inner.get("content", []) if isinstance(inner, dict) else []
            for item in content if isinstance(content, list) else []:
                if item.get("type") == "tool_result":
                    results[item.get("tool_use_id")] = (_tool_output(item.get("content")), moment)
                elif item.get("type") == "tool_use" and message.get("type") == "assistant":
                    calls.append((item, moment))
        for item, moment in calls:
            arguments = item.get("input", {})
            output, finished = results.get(item.get("id"), ("", None))
            if item.get("name") == "Bash":
                command = arguments.get("command", "")
                events.append(Event("command", command, output, moment, finished))
            elif item.get("name") in {"Write", "Edit", "MultiEdit"}:
                path = os.path.normpath(session_dir / arguments.get("file_path", ""))
                events.append(Event("write", path, "", moment, finished))
        return events
    lines = [line for _, line in entries]
    for index, line in enumerate(lines):
        if line == "exec":
            # A command, possibly multi-line, runs until its " succeeded/exited in" status;
            # its output runs until the next transcript marker.
            end = next(
                (j for j in range(index + 1, len(lines)) if COMMAND_STATUS.match(lines[j])),
                index + 2,
            )
            stop = next(
                (j for j in range(end + 1, len(lines)) if lines[j] in CODEX_MARKERS), len(lines)
            )
            finished = entries[min(stop, len(entries)) - 1][0]
            command, output = "\n".join(lines[index + 1 : end]), "\n".join(lines[end:stop])
            events.append(Event("command", command, output, entries[index][0], finished))
        elif line == "apply patch" and lines[index + 1 : index + 2] == ["patch: completed"]:
            for path in lines[index + 2 :]:
                if not path or path in CODEX_MARKERS or path.startswith("diff --git"):
                    break
                path = os.path.normpath(session_dir / path)
                events.append(Event("write", path, "", entries[index][0], entries[index][0]))
    return events


def _doctor_passed(event):
    return (
        event.kind == "command"
        and DOCTOR_PASSED.search(event.output)
        and any(DOCTOR.match(part) for part in simple_commands(event.value))
    )


def _locator_ran(event):
    return event.kind == "command" and any(
        LOCATOR.match(part) for part in simple_commands(event.value)
    )


def _may_write_tree(event, roots):
    if event.kind == "write":
        return any(Path(event.value).is_relative_to(root) for root in roots)
    text = HARMLESS_REDIRECT.sub("", event.value)
    if TREE_PATH.search(text) and (WRITE_OP.search(text) or TREE_CD.search(text)):
        return True
    # Any script other than the entry locator may generate cases.
    return any(
        INTERPRETER.match(part)
        and any(Path(m.group()).name != "locate.py" for m in SCRIPT.finditer(part))
        for part in simple_commands(event.value)
    )


def authoring_order_errors(host, log, session_dir, workspace, start, changed, changed_at=None):
    """A passing doctor, and for a workspace start the entry locator, must precede the
    first change to tests/ or coverage/.

    A timestamped `log` (a timeline from `run_host`) is compared with `changed_at`, the
    earliest time a change there was observed (None: nothing changed), or with the start
    of an earlier command that looks like a write. A text transcript falls back to
    finding the first write; if the host `changed` either directory but no write is
    found, it fails."""
    events = session_events(host, log, session_dir)
    roots = (workspace / "tests", workspace / "coverage")
    errors = []
    if not isinstance(log, str):
        if changed_at is None:
            return []
        changed_at = min([changed_at, *(e.started for e in events if _may_write_tree(e, roots))])
        if not any(_doctor_passed(e) and e.finished <= changed_at for e in events):
            errors.append("workspace doctor did not pass before the first write")
        if start == "workspace" and not any(
            _locator_ran(e) and e.started <= changed_at for e in events
        ):
            errors.append("the entry locator did not run before the first write")
        return errors
    first = next((i for i, e in enumerate(events) if _may_write_tree(e, roots)), None)
    if first is None:
        return (
            ["tests or coverage changed but no write was found in the transcript"]
            if changed
            else []
        )
    if not any(_doctor_passed(e) for e in events[:first]):
        errors.append("workspace doctor did not pass before the first write")
    if start == "workspace" and not any(_locator_ran(e) for e in events[:first]):
        errors.append("the entry locator did not run before the first write")
    return errors


def tree_changed_at(workspace, before):
    """Earliest modification time of a new or changed file under tests/ or coverage/."""
    times = []
    for name, old in zip(("tests", "coverage"), before, strict=True):
        for relative, digest in snapshot(workspace / name).items():
            if old.get(relative) != digest:
                times.append((workspace / name / relative).stat().st_mtime)
    return min(times, default=None)


# application.properties exposes health and release under /actuator.
EXTENDED_PATHS = {"/api/promo", "/actuator/release", "/actuator/health"}


def write_extended_fixture(business):
    """Conditional and management routes (S1) plus a contract/implementation conflict (S12)."""
    (business / "Promo.java").write_text(
        "@RestController\n"
        '@ConditionalOnProperty(name = "shop.promo.enabled", havingValue = "true")\n'
        'public class Promo {\n  @GetMapping("/api/promo")\n'
        '  public String promo() { return "PROMO"; }\n}\n'
    )
    (business / "ReleaseEndpoint.java").write_text(
        '@Component\n@Endpoint(id = "release")\npublic class ReleaseEndpoint {\n'
        '  @ReadOperation\n  public String release() { return "1.4.0"; }\n}\n'
    )
    (business / "application.properties").write_text(
        "management.endpoints.web.exposure.include=health,release\nshop.promo.enabled=false\n"
    )
    (business / "Refunds.java").write_text(
        '@RestController\npublic class Refunds {\n'
        '  @GetMapping("/api/refunds/{id}")\n'
        "  public ResponseEntity<String> get(@PathVariable long id) {\n"
        '    return ResponseEntity.ok(store.getOrDefault(id, ""));\n  }\n}\n'
    )
    contract = business / "API.md"
    contract.write_text(
        contract.read_text()
        + "GET /api/refunds/{id} returns 200 with the refund, or 404 when no refund has that id."
        " Refund id 0 never exists.\n"
        "GET /api/promo is available only when shop.promo.enabled is true.\n"
        "The management endpoint GET /actuator/release returns the deployed release name.\n"
    )


REFUND_LOOKUP = re.compile(
    r"refunds/(\{[^}]*\}|0\b)|Refunds\.java|不存在的退款|退款\s*ids?(?![a-z])"
    r"|unknown (refund )?ids?(?![a-z])|missing refund|refund (ids?|lookup)(?![a-z])",
    re.I,
)


def extended_fixture_errors(workspace):
    """Check S1/S12 behaviour without executing generated code."""
    from apitest.coverage import load_inventory
    from apitest.runner.assertions import AssertionMismatch, assert_response
    from apitest.schema.case_v1 import Case

    errors = []
    endpoints = {e.path: e for _, e in load_inventory(workspace / "coverage")}
    refund_paths = [p for p in endpoints if re.fullmatch(r"/api/refunds/\{[^}]*\}", p)]
    missing = sorted(EXTENDED_PATHS - endpoints.keys())
    missing += [] if refund_paths else ["/api/refunds/{id}"]
    errors.extend(f"inventory lacks {path}" for path in missing)
    promo = endpoints.get("/api/promo")
    if promo is not None and not promo.conditions and not any(
        "promo" in (row.reason or "").lower() for row in promo.rows
    ):
        errors.append("/api/promo inventory records no enabling condition")

    def accepts(expected, status):
        try:
            assert_response(status=status, body="", text="", expected=expected)
        except (AssertionMismatch, ValueError, TypeError):
            return False
        return True

    asserts_contract = False
    for path in (workspace / "tests").rglob("case_*.yaml"):
        try:
            case = Case.model_validate(yaml.safe_load(path.read_text()))
        except Exception:
            continue
        for step in case.steps:
            if step.request.method != "GET" or urlsplit(step.request.path).path != "/api/refunds/0":
                continue
            expected = {"status": step.assert_.status} if step.assert_.status is not None else {}
            if not expected:
                continue
            if case.expectation == "confirmed" and accepts(expected, 200):
                errors.append(f"{path.name}/{step.name}: asserts the implementation's 200")
            if accepts(expected, 404) and not accepts(expected, 200):
                asserts_contract = True
    if not asserts_contract:
        errors.append("no case asserts the contract's 404 for a missing refund")
    # Only a defect note about the refund lookup, citing the contract's 404 and the
    # implementation's 200, certifies the conflict. Prose cannot be parsed reliably, so a
    # conflict stated only in the final report is left for manual confirmation.
    recorded = any(
        REFUND_LOOKUP.search(text) and "404" in text and "200" in text
        for text in (p.read_text(errors="replace") for p in (workspace / "defects").glob("*.md"))
    )
    if not recorded:
        errors.append(
            "no defects/*.md note records the refund lookup conflict; "
            "check the final report manually"
        )
    return errors


def expected_paths(scope, stale_endpoint, extended):
    """Endpoints the fixture exposes; extended refund lookups are checked separately."""
    expected = {"/api/refunds/quote", "/api/health"} if scope == "whole" else {"/api/refunds/quote"}
    if stale_endpoint:
        expected.add("/api/retired")
    return expected | (EXTENDED_PATHS if extended else set())


def comparable_paths(paths, extended):
    """Drop what the extended fixture checks separately (the refund lookup) or allows but
    does not require (the actuator discovery page that exposing endpoints implies)."""
    optional = r"/api/refunds/\{[^}]*\}|/actuator/?"
    return {p for p in paths if not (extended and re.fullmatch(optional, p))}


REPOSITORY = Path(__file__).resolve().parents[1]


def reevaluate(artifacts, repository=REPOSITORY):
    """Re-run the oracles on retained artifacts after an oracle change, without the host.

    probe.json stays untouched; each run writes `reevaluation-<oracle commit>.json`.
    Paths the host logged are rebased when the artifacts have moved. Service calls,
    business snapshots and static validation do not depend on the oracles and stay
    in probe.json. Sandbox and sync-failure runs author nothing, so only the order
    check applies to them."""
    from apitest.coverage import load_inventory

    report = json.loads((artifacts / "probe.json").read_text())
    workspace = artifacts / "shop-e2e"
    start = report.get("start_in", "business")
    target = tomllib.loads((workspace / "repo2test.toml").read_text())["target"]["path"]
    session = workspace if start == "workspace" else Path(os.path.normpath(workspace / target))
    events_file, moved = artifacts / "host-events.jsonl", str(artifacts)
    if events_file.is_file() and "tree_changed_at" in report:
        log = [
            (entry["time"], entry["stream"], entry["line"].replace(report["artifacts"], moved))
            for entry in map(json.loads, events_file.read_text().splitlines())
        ]
        changed_at = report["tree_changed_at"]
    else:
        log = (artifacts / "host.log").read_text().replace(report["artifacts"], str(artifacts))
        changed_at = None
    extended = report.get("extended_fixture", False)
    authoring = report["scenario"] not in {"sandbox", "sync-failure"}
    commit = run(["git", "rev-parse", "HEAD"], repository).strip()
    modified = bool(
        run(
            ["git", "status", "--porcelain", "--", "scripts/verify_host_authoring.py", "src"],
            repository,
        ).strip()
    )
    record = {
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "oracle_commit": commit,
        "oracle_modified": modified,
        "original_passed": report["passed"],
        "order_evidence": "transcript" if isinstance(log, str) else "timed",
        "authoring_order_errors": authoring_order_errors(
            report["host"], log, session, workspace, start, report.get("cases", 0) > 0, changed_at
        ),
    }
    if authoring:
        paths = comparable_paths(
            {e.path for _, e in load_inventory(workspace / "coverage")}, extended
        )
        stale = report.get("stale_endpoint_reviewed") is not None
        record |= {
            "paths_match": paths == expected_paths(report["scope"], stale, extended),
            "paths": sorted(paths),
            "fixture_oracle_errors": fixture_oracle(workspace, report["scope"]),
            "extended_fixture_errors": extended_fixture_errors(workspace) if extended else [],
        }
    name = f"reevaluation-{commit[:12]}{'-modified' if modified else ''}.json"
    (artifacts / name).write_text(json.dumps(record, indent=2))
    return record


def seed_partial_workspace(workspace):
    source = tomllib.loads((workspace / "repo2test.toml").read_text())["reference"]["commit"]
    key = "shop|GET|/api/health|happy|up"
    case = {
        "schema": "v1",
        "name": "health--manual",
        "service": "shop",
        "source_commit": source,
        "covers": [{"key": key}],
        "steps": [
            {
                "name": "health",
                "request": {"method": "GET", "path": "/api/health"},
                "assert": {"status": 200, "text": "UP"},
            }
        ],
    }
    path = workspace / "tests/shop/health/case_health--manual.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Manual assertion rationale: preserve this exact case.\n"
        + json.dumps(case, indent=2)
        + "\n"
    )
    for endpoint, coverage_key, status in (
        ("/api/health", key, "authored"),
        ("/api/refunds/quote", "shop|GET|/api/refunds/quote|happy|valid-amount", "uncovered"),
    ):
        (workspace / "coverage" / (endpoint.rsplit("/", 1)[1] + ".yaml")).write_text(
            json.dumps(
                {
                    "service": "shop",
                    "method": "GET",
                    "path": endpoint,
                    "rows": [
                        {
                            "key": coverage_key,
                            "status": status,
                            "source_commit": source,
                            "evidence": [
                                {
                                    "file": "API.md",
                                    "line": 3 if status == "authored" else 4,
                                    "kind": "requirement",
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n"
        )
    return path, path.read_bytes()


def seed_retired_endpoint(workspace, source):
    key = "shop|GET|/api/retired|happy|legacy"
    path = workspace / "tests/shop/retired/case_retired--manual.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("# Human-maintained legacy case: retain for review.\n" + json.dumps({
        "schema": "v1", "name": "retired--manual", "service": "shop",
        "source_commit": source, "covers": [{"key": key}],
        "steps": [{"name": "retired", "request": {"method": "GET", "path": "/api/retired"},
                   "assert": {"status": 200, "text": "legacy"}}],
    }, indent=2) + "\n")
    (workspace / "coverage/retired.yaml").write_text(json.dumps({
        "service": "shop", "method": "GET", "path": "/api/retired",
        "evidence": [{"file": "Retired.java", "line": 1, "kind": "route"}],
        "rows": [{"key": key, "status": "authored", "source_commit": source,
                  "evidence": [{"file": "API.md", "line": 8, "kind": "requirement"}]}],
    }, indent=2) + "\n")
    return path, path.read_bytes()


def exit_on_termination_signals():
    """Turn SIGTERM and SIGHUP (a closed terminal) into SystemExit, so `finally` blocks
    still stop the host, remove the temporary entry and shut down the listener.

    The first signal wins: repeats, such as a terminal's second SIGHUP, are ignored so the
    cleanup can finish. A SIGHUP already ignored, as under `nohup`, stays ignored."""
    handled = [s for s in (signal.SIGTERM, signal.SIGHUP) if signal.getsignal(s) != signal.SIG_IGN]

    def stop(number, _frame):
        for signum in handled:
            signal.signal(signum, signal.SIG_IGN)
        sys.exit(128 + number)

    for signum in handled:
        signal.signal(signum, stop)


def main():
    exit_on_termination_signals()
    if sys.argv[1:2] == ["reevaluate"]:
        for artifacts in sys.argv[2:]:
            print(json.dumps(reevaluate(Path(artifacts).resolve())))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("host", choices=["codex", "claude"])
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--mode", choices=["explicit", "natural"], default="explicit")
    parser.add_argument("--scope", choices=["whole", "feature"], default="whole")
    parser.add_argument(
        "--scenario", choices=["authoring", "sandbox", "missing-url", "resume", "sync-failure"],
        default="authoring",
    )
    parser.add_argument("--installation", choices=["user", "project"], default="user")
    parser.add_argument("--warm", action="store_true", help="Pre-sync the runner; default is cold.")
    parser.add_argument("--stale-endpoint", action="store_true", help="Seed a removed route case.")
    parser.add_argument(
        "--start-in", choices=["business", "workspace"], default="business",
        help="Directory the host session starts in (S18 uses the test workspace).",
    )
    parser.add_argument(
        "--extended-fixture", action="store_true",
        help="Add conditional/management routes and a contract conflict (S1, S12).",
    )
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-turns", type=int, default=80, help="Claude Code turn limit.")
    args = parser.parse_args()
    executable = shutil.which(args.host)
    if not executable:
        raise SystemExit(f"{args.host} not installed")
    if args.scenario == "sandbox" and (args.host != "codex" or args.warm):
        parser.error("The sandbox scenario requires Codex and a cold runner")
    if args.scenario == "resume" and args.scope != "whole":
        parser.error("The resume fixture covers the whole repository")
    if args.scenario == "sync-failure" and args.warm:
        parser.error("The sync-failure scenario requires a cold runner")
    if args.stale_endpoint and args.scenario != "resume":
        parser.error("--stale-endpoint requires --scenario resume")
    if args.start_in == "workspace" and (
        args.installation != "user" or args.scenario != "authoring"
    ):
        parser.error("--start-in workspace requires a user-level entry and authoring")
    if args.extended_fixture and (args.scope != "whole" or args.scenario != "authoring"):
        parser.error("--extended-fixture requires whole-repository authoring")
    # /tmp is writable in Codex's sandbox even without --add-dir. Keep a denied
    # sibling outside it so the negative probe actually crosses the boundary.
    artifacts = Path.home() / ".cache/repo2test-host-probes"
    artifacts.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f"{args.host}-{args.mode}-{args.scenario}-", dir=artifacts))
    # From a workspace start, only repo2test.toml leads to this unconventional sibling.
    business = root / ("refund-service" if args.start_in == "workspace" else "shop")
    workspace = root / "shop-e2e"
    business.mkdir()
    run(["git", "init", "-q"], business)
    run(["git", "config", "user.name", "Skill probe"], business)
    run(["git", "config", "user.email", "probe@example.invalid"], business)
    (business / ".gitignore").write_text(".agents/\n.claude/\n")
    (business / "pom.xml").write_text(
        "<project><dependencies><dependency><groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-web</artifactId></dependency>"
        "</dependencies></project>\n"
    )
    (business / "Application.java").write_text(
        "@SpringBootApplication\npublic class Application {}\n"
    )
    (business / "Shop.java").write_text(
        '@RestController\n@RequestMapping("/api")\npublic class Shop {\n'
        '  @GetMapping("/health")\n  public String health() { return "UP"; }\n'
        '  @GetMapping("/refunds/quote")\n'
        "  public int quote(@RequestParam @Min(1) @Max(100) int amount) { return amount; }\n}\n"
    )
    (business / "API.md").write_text(
        "# HTTP contract\n\nGET /api/health returns 200 with text UP.\n"
        "GET /api/refunds/quote?amount=N accepts integer amounts from 1 to 100 inclusive.\n"
        "Valid requests return 200 and the amount as a JSON number.\n"
        "Missing amount, a non-integer, or values outside that range return 400.\n"
        "Both endpoints are public and read-only. There is no DB or authentication.\n"
    )
    if args.stale_endpoint:
        retired = business / "Retired.java"
        retired.write_text('@RestController class Retired { @GetMapping("/api/retired") '
                           'String retired() { return "legacy"; } }\n')
        contract = business / "API.md"
        current_contract = contract.read_text()
        contract.write_text(current_contract + "GET /api/retired returns 200 with text legacy.\n")
        run(["git", "add", "."], business)
        run(["git", "commit", "-qm", "previous contract"], business)
        retired.unlink()
        contract.write_text(current_contract)
    if args.extended_fixture:
        write_extended_fixture(business)
    run(["git", "add", "."], business)
    run(["git", "commit", "-qm", "reference contract"], business)
    bootstrap = Path(__file__).resolve().parents[1] / ".venv/bin/apitest"
    run(
        [
            str(bootstrap),
            "workspace",
            "init",
            str(workspace),
            "--target",
            str(business),
            "--ref",
            "HEAD",
            "--runner-wheel",
            str(args.wheel.resolve()),
            "--runner-owner",
            "@runner-maintainers",
            "--ci",
            "github",
            "--offline",
        ],
        root,
    )
    if args.warm:
        run(["uv", "sync", "--locked", "--offline"], workspace)
    calls = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), recording_handler(calls))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner = [str(bootstrap), "--workspace", str(workspace)]
    if args.scenario != "missing-url":
        run(
            [
                *runner,
                "workspace",
                "configure",
                "--service",
                "shop",
                "--url",
                f"http://127.0.0.1:{server.server_port}",
                "--environment",
                "non-production",
            ],
            workspace,
        )
    prefix = (
        ("$repo2test " if args.host == "codex" else "/repo2test ")
        if args.mode == "explicit"
        else ""
    )
    prompt = prefix + authoring_prompt(args.mode, args.scope, args.scenario, args.start_in)
    preserved = seed_partial_workspace(workspace) if args.scenario == "resume" else None
    retired_case = (
        seed_retired_endpoint(workspace, run(["git", "rev-parse", "HEAD^"], business).strip())
        if args.stale_endpoint else None
    )
    workspace_before = snapshot(workspace)
    tree_before = [snapshot(workspace / name) for name in ("tests", "coverage")]
    tree_roots = (workspace / "tests", workspace / "coverage")
    if args.scope == "feature":
        # A dirty unrelated login change must not broaden the selected feature.
        (business / "Login.java").write_text(
            '@RestController class Login { @PostMapping("/login") void login() {} }\n'
        )
    before = business_snapshot(business)
    grants = (
        []
        if args.scenario == "sandbox"
        else [workspace, Path(run(["uv", "cache", "dir"], workspace).strip())]
    )
    host_env = os.environ.copy()
    if args.scenario == "sync-failure":
        cache = root / "empty-uv-cache"
        cache.mkdir()
        host_env.update(UV_OFFLINE="1", UV_CACHE_DIR=str(cache))
        grants = [workspace, cache]
    if args.start_in == "workspace":
        # The session owns the workspace; the business repo is only read (Claude needs it added).
        cache_dir = Path(run(["uv", "cache", "dir"], workspace).strip())
        grants = [business, cache_dir] if args.host == "claude" else [cache_dir]
    session_dir = workspace if args.start_in == "workspace" else business
    extra_dirs = [value for path in grants for value in ("--add-dir", str(path))]
    if args.host == "codex":
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            *extra_dirs,
            prompt,
        ]
    else:
        command = [
            executable,
            "--print",
            "--max-turns",
            str(args.max_turns),
            "--verbose",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "acceptEdits",
            *extra_dirs,
            "--allowedTools",
            "Read,Write,Edit,Bash,Skill,Glob,Grep",
            "--",
            prompt,
        ]
    try:
        with installed_entry(args.host, workspace, business, args.installation) as entry:
            code, timeline, first_seen = run_host(
                command, session_dir, host_env, args.timeout, watch=tree_roots
            )
        final_changes = tree_changed_at(workspace, tree_before)
        changed_at = min((t for t in (first_seen, final_changes) if t is not None), default=None)
        streams = {
            name: "".join(line + "\n" for _, stream, line in timeline if stream == name)
            for name in ("stdout", "stderr")
        }
        log = streams["stdout"] + streams["stderr"]
        response = "" if code == 124 else final_response(args.host, streams["stdout"])
        (root / "host.log").write_text(log)
        (root / "host-events.jsonl").write_text(
            "".join(
                json.dumps({"time": moment, "stream": stream, "line": line}) + "\n"
                for moment, stream, line in timeline
            )
        )
        (root / "final.log").write_text(response)
        # Retain observations even if a later evaluator fails.
        (root / "observations.json").write_text(
            json.dumps({"returncode": code, "service_calls": calls}, indent=2)
        )
        validation = subprocess.run(
            [
                *runner,
                "validate",
                *([] if args.scenario == "missing-url" else ["--profile", "local"]),
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        (root / "validate.log").write_text(validation.stdout + validation.stderr)
        cases = list((workspace / "tests").rglob("case_*.yaml"))
        coverage = json.loads(run([*runner, "coverage"], workspace))
        paths = {row["path"] for row in coverage["rows"]}
        expected = expected_paths(args.scope, args.stale_endpoint, args.extended_fixture)
        paths = comparable_paths(paths, args.extended_fixture)
        extended_errors = extended_fixture_errors(workspace) if args.extended_fixture else []
        unchanged = before == business_snapshot(business)
        oracle_errors = fixture_oracle(workspace, args.scope)
        changed = [snapshot(workspace / name) for name in ("tests", "coverage")] != tree_before
        order_errors = authoring_order_errors(
            args.host, timeline, session_dir, workspace, args.start_in, changed, changed_at
        )
        preservation_ok = not preserved or (
            preserved[0].is_file() and preserved[0].read_bytes() == preserved[1]
        )
        stale_reviewed = not retired_case or (
            retired_case[0].is_file()
            and retired_case[0].read_bytes() == retired_case[1]
            and any(r["path"] == "/api/retired" and r["status"] == "needs-review"
                    for r in coverage["rows"])
        )
        passed = (
            code == 0
            and bool(response.strip())
            and len(cases) >= 2
            and validation.returncode == 0
            and paths == expected
            and not calls
            and unchanged
            and preservation_ok
            and stale_reviewed
            and not oracle_errors
            and not extended_errors
            and not order_errors
            and (workspace / ".venv/bin/apitest").is_file()
        )
        if args.scenario == "sandbox":
            passed = (
                code == 0
                and not calls
                and unchanged
                and workspace_before == snapshot(workspace)
                and "--add-dir" in response
                and not (workspace / ".venv").exists()
            )
        elif args.scenario == "missing-url":
            passed = (
                code == 0
                and validation.returncode == 0
                and not calls
                and unchanged
                and "URL" in response.upper()
                and service_url_is_missing(workspace)
                and not order_errors
            )
        elif args.scenario == "sync-failure":
            passed = (
                code == 0
                and not calls
                and unchanged
                and not cases
                and "uv sync" in response
                and ("offline" in log.lower() or "cache" in log.lower())
                and not (workspace / ".venv/bin/apitest").exists()
            )
        report = {
            "host": args.host,
            "host_version": run([executable, "--version"], business).strip(),
            "mode": args.mode,
            "scope": args.scope,
            "scenario": args.scenario,
            "installation": args.installation,
            "start_in": args.start_in,
            "extended_fixture": args.extended_fixture,
            "extended_fixture_errors": extended_errors,
            "entry": str(entry),
            "runner_pre_synced": args.warm,
            "dependency_fault": "empty offline cache" if args.scenario == "sync-failure" else None,
            "host_turn_limit": args.max_turns if args.host == "claude" else None,
            "granted_directories": [str(path) for path in grants],
            "prompt": prompt,
            "manual_case_preserved": preservation_ok,
            "stale_endpoint_reviewed": stale_reviewed if retired_case else None,
            "business_unchanged": unchanged,
            "final_response_present": bool(response.strip()),
            "fixture_oracle_errors": oracle_errors,
            "authoring_order_errors": order_errors,
            "order_evidence": "timed",
            "tree_changed_at": changed_at,
            "tree_first_seen": first_seen,
            "passed": passed,
            "returncode": code,
            "cases": len(cases),
            "paths": sorted(paths),
            "service_calls": calls,
            "artifacts": str(root),
        }
        (root / "probe.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
