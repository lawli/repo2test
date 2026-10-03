import shutil
from pathlib import Path

import pytest

import apitest
from apitest.coverage import coverage_view
from apitest.schema.case_v1 import Case
from apitest.validation import validate_workspace
from apitest.workspace import Workspace
from scripts.verify_host_authoring import (
    EXTENDED_PATHS,
    authoring_order_errors,
    authoring_prompt,
    business_snapshot,
    comparable_paths,
    expected_paths,
    extended_fixture_errors,
    final_response,
    fixture_oracle,
    installed_entry,
    recording_handler,
    reevaluate,
    run_host,
    seed_partial_workspace,
    seed_retired_endpoint,
    service_url_is_missing,
    snapshot,
    tree_changed_at,
)


@pytest.mark.parametrize("host, folder", [("codex", ".agents"), ("claude", ".claude")])
def test_user_entry_probe_uses_user_skill_directory_and_cleans_up_on_interruption(
    tmp_path, host, folder
):
    workspace, business, user_root = (tmp_path / name for name in ("workspace", "business", "user"))
    workspace.mkdir()
    business.mkdir()
    assets = Path(apitest.__file__).parent / "assets"
    shutil.copytree(assets / "entry", workspace / "tools/entry")
    shutil.copy2(assets / "install_entry.py", workspace / "tools/install_entry.py")
    destination = user_root / folder / "skills/repo2test"
    with (
        pytest.raises(TimeoutError),
        installed_entry(host, workspace, business, "user", user_root=user_root) as entry,
    ):
        assert entry == destination
        assert (entry / "SKILL.md").is_file()
        assert not (business / folder).exists()
        raise TimeoutError("simulated host timeout")
    assert not destination.exists()
    destination.mkdir(parents=True)
    sentinel = destination / "SKILL.md"
    sentinel.write_text("User's unrelated or older installation\n")
    with (
        pytest.raises(RuntimeError),
        installed_entry(host, workspace, business, "user", user_root=user_root),
    ):
        pytest.fail("A different existing installation must not be overwritten")
    assert sentinel.read_text() == "User's unrelated or older installation\n"


def test_natural_whole_repository_prompt_does_not_name_the_skill():
    prompt = authoring_prompt("natural", "whole", "authoring")
    assert "为这个 repo 创建 test cases" in prompt
    assert "repo2test" not in prompt and "apitest" not in prompt
    assert "不请求" not in prompt and "只创建" not in prompt


@pytest.mark.parametrize(
    "method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CUSTOM"]
)
def test_service_probe_records_every_method_including_unsupported_ones(method):
    import io

    calls = []
    handler = recording_handler(calls).__new__(recording_handler(calls))
    handler.raw_requestline = f"{method} /login HTTP/1.1\r\n".encode()
    handler.rfile = io.BytesIO(b"Host: localhost\r\nContent-Length: 0\r\n\r\n")
    handler.wfile = io.BytesIO()
    assert handler.parse_request()
    assert calls == [{"method": method, "path": "/login"}]


def test_business_snapshot_detects_edits_to_already_dirty_files_and_new_commits(tmp_path):
    from scripts.verify_host_authoring import run

    run(["git", "init", "-q"], tmp_path)
    run(["git", "config", "user.name", "Test"], tmp_path)
    run(["git", "config", "user.email", "test@example.invalid"], tmp_path)
    source = tmp_path / "source.txt"
    source.write_text("original")
    run(["git", "add", "."], tmp_path)
    run(["git", "commit", "-qm", "base"], tmp_path)
    source.write_text("manual edit")
    before = business_snapshot(tmp_path)
    source.write_text("overwritten by agent")
    after = business_snapshot(tmp_path)
    assert before["status"] == after["status"] and before != after
    source.write_text("original")
    before = business_snapshot(tmp_path)
    run(["git", "commit", "--allow-empty", "-qm", "agent commit"], tmp_path)
    assert before != business_snapshot(tmp_path)


def test_fixture_oracle_requires_boundary_assertions_and_detects_wrong_contract(tmp_path):
    import yaml

    tests = tmp_path / "tests/shop"
    tests.mkdir(parents=True)
    for name, amount, status in (
        ("health", None, 200),
        ("interior", 50, 200),
        ("minimum", 1, 200),
        ("maximum", 100, 200),
        ("below", 0, 400),
        ("above", 101, 400),
        ("missing", None, 400),
        ("malformed", "abc", 400),
    ):
        request = {
            "method": "GET",
            "path": "/api/health" if name == "health" else "/api/refunds/quote",
        }
        if amount is not None:
            request["params"] = {"amount": amount}
        assertion = {"status": status}
        if name == "health":
            assertion["text"] = "UP"
        elif status == 200:
            assertion["text"] = str(amount)
        data = {
            "schema": "v1",
            "name": name,
            "service": "shop",
            "steps": [{"name": name, "request": request, "assert": assertion}],
        }
        (tests / f"case_{name}.yaml").write_text(yaml.safe_dump(data))
    assert fixture_oracle(tmp_path, "whole") == []
    path = tests / "case_minimum.yaml"
    case = yaml.safe_load(path.read_text())
    case["steps"][0]["assert"] = {"status": 200}
    path.write_text(yaml.safe_dump(case))
    assert "missing contract assertion: minimum" in fixture_oracle(tmp_path, "whole")
    case["steps"][0]["assert"] = {"status": 500}
    path.write_text(yaml.safe_dump(case))
    assert any("contradicts minimum contract" in e for e in fixture_oracle(tmp_path, "whole"))
    path.unlink()
    assert "missing contract assertion: minimum" in fixture_oracle(tmp_path, "whole")


def test_probe_guidance_must_be_in_final_response_not_only_tool_or_prompt_logs():
    tool_event = '{"type": "assistant", "message": "Skill says ask for a URL"}\n'
    result_event = '{"type": "result", "result": "Please provide the service URL."}\n'
    assert final_response("claude", tool_event) == ""
    assert final_response("claude", tool_event + result_event) == "Please provide the service URL."
    assert final_response("codex", " Please provide the service URL.\n") == (
        "Please provide the service URL."
    )


@pytest.mark.parametrize(
    "url, dotenv, missing",
    [
        ("", "", True),
        ("${PROBE_SHOP_URL}", "", True),
        ("${PROBE_SHOP_URL}", "PROBE_SHOP_URL=\n", True),
        ("${PROBE_SHOP_URL}", "PROBE_SHOP_URL=http://localhost:8080\n", False),
        ("http://localhost:8080", "", False),
    ],
)
def test_url_probe_accepts_missing_placeholder_but_rejects_invented_url(
    tmp_path, monkeypatch, url, dotenv, missing
):
    import yaml

    monkeypatch.delenv("PROBE_SHOP_URL", raising=False)
    monkeypatch.delenv("PROBE_MISSING_TOKEN", raising=False)
    (tmp_path / "repo2test.toml").write_text("format = 1\n")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "base.yaml").write_text(
        yaml.safe_dump({"services": {"shop": {"base_url": url, "token": "${PROBE_MISSING_TOKEN}"}}})
    )
    (profiles / "local.yaml").write_text("extends: base.yaml\n")
    (tmp_path / ".env").write_text(dotenv)
    assert service_url_is_missing(tmp_path) is missing


def test_resume_fixture_retains_manual_case_and_uncovered_denominator(
    tmp_path, install_pinned_runner
):
    import yaml

    (tmp_path / "coverage").mkdir()
    (tmp_path / "repo2test.toml").write_text('[reference]\ncommit = "' + "a" * 40 + '"\n')
    install_pinned_runner(tmp_path)
    path, original = seed_partial_workspace(tmp_path)
    case = Case.model_validate(yaml.safe_load(path.read_text()))
    assert path.read_bytes() == original and original.startswith(b"# Manual")
    workspace = Workspace.load(tmp_path)
    assert validate_workspace(workspace) == []
    view = coverage_view(workspace.path("coverage"), [case.model_dump()])
    assert view["authored"] == view["pending"] == 1
    assert "上次任务已中断" in authoring_prompt("natural", "whole", "resume")


def test_retired_fixture_retains_case_when_row_is_marked_for_review(
    tmp_path, install_pinned_runner
):
    import yaml

    (tmp_path / "coverage").mkdir()
    (tmp_path / "repo2test.toml").write_text('[reference]\ncommit = "' + "a" * 40 + '"\n')
    install_pinned_runner(tmp_path)
    path, original = seed_retired_endpoint(tmp_path, "b" * 40)
    row_path = tmp_path / "coverage/retired.yaml"
    endpoint = yaml.safe_load(row_path.read_text())
    endpoint["rows"][0]["status"] = "needs-review"
    endpoint["rows"][0]["reason"] = "Route removed from deployed reference"
    row_path.write_text(yaml.safe_dump(endpoint))
    workspace = Workspace.load(tmp_path)
    assert validate_workspace(workspace) == [] and path.read_bytes() == original
    case = yaml.safe_load(path.read_text())
    results = {"cases": [{**case, "outcome": "PASS", "expectation": "confirmed"}]}
    view = coverage_view(workspace.path("coverage"), [case], results)
    assert view["authored"] == view["needs_review"] == 1
    assert view["verified"] == 0


def test_workspace_start_prompt_names_neither_repository_nor_the_skill():
    prompt = authoring_prompt("natural", "whole", "authoring", "workspace")
    assert "当前目录就是业务仓库的测试仓库" in prompt
    assert not any(name in prompt for name in ("repo2test", "shop-e2e", "refund-service"))


PASSED = '{"workspace": "ws", "problems": [], "synced": false}'
FAILED = '{"workspace": "ws", "problems": ["asset drift"], "synced": false}'


def _claude_log(*calls):
    """Claude stream-json with each (tool, input[, output]) call and its result."""
    import json

    lines = []
    for index, (name, value, *output) in enumerate(calls):
        call = {"type": "tool_use", "id": f"t{index}", "name": name, "input": value}
        result = {"type": "tool_result", "tool_use_id": f"t{index}", "content": "".join(output)}
        lines.append(json.dumps({"type": "assistant", "message": {"content": [call]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [result]}}))
    return "\n".join(lines)


def _codex_command(command, output=""):
    return f"exec\n/bin/bash -lc {command!r} in .\n succeeded in 1ms:\n{output}\n"


LOCATE = ("Bash", {"command": "python3 scripts/locate.py --start ."})
DOCTOR = ("Bash", {"command": "uv run --locked apitest workspace doctor 2>&1 | tail"}, PASSED)
WRITE = ("Bash", {"command": "cat > coverage/shop.yaml <<EOF\nrows: []\nEOF"})


def test_order_check_requires_locator_and_passing_doctor_before_the_first_write(tmp_path):
    workspace = tmp_path / "shop-e2e"

    def errors(*calls, start="workspace", changed=True):
        log = _claude_log(*calls)
        return authoring_order_errors("claude", log, workspace, workspace, start, changed)

    assert errors(LOCATE, DOCTOR, WRITE) == []
    quoted = ("Bash", {"command": 'cd skill && python3 "scripts/locate.py" --start /w'})
    assert errors(quoted, DOCTOR, WRITE) == []
    written_first = ("Write", {"file_path": "tests/shop/case_a.yaml"})
    assert errors(written_first, LOCATE, DOCTOR) == [
        "workspace doctor did not pass before the first write",
        "the entry locator did not run before the first write",
    ]
    doctor_failed = ("Bash", {"command": "uv run --locked apitest workspace doctor"}, FAILED)
    mentioned = ("Bash", {"command": 'grep -n "apitest workspace doctor" SKILL.md'}, PASSED)
    echoed = ("Bash", {"command": "echo uv run apitest workspace doctor"}, PASSED)
    for fake in (doctor_failed, mentioned, echoed):
        assert errors(LOCATE, fake, WRITE) == [
            "workspace doctor did not pass before the first write"
        ]
    for write in (
        ("Bash", {"command": "cp /tmp/case.yaml tests/shop/case_a.yaml"}),
        ("Bash", {"command": "python -c \"open('coverage/x.yaml', 'w').write('')\""}),
        ("Bash", {"command": "cd tests && cat > shop/case_a.yaml"}),
        ("Write", {"file_path": "../shop-e2e/tests/case_a.yaml"}),
    ):
        assert errors(LOCATE, write, DOCTOR), write
    assert errors(("Bash", {"command": "ls tests/ coverage/ 2>&1"}), changed=False) == []
    assert errors(("Bash", {"command": "ls tests/"})) == [
        "tests or coverage changed but no write was found in the transcript"
    ]


def test_order_check_skips_claude_events_whose_message_is_text(tmp_path):
    import json

    workspace = tmp_path / "shop-e2e"
    denied = json.dumps(
        {"type": "system", "subtype": "permission_denied", "message": "Dangerous rm operation"}
    )
    log = "\n".join([_claude_log(LOCATE, DOCTOR), denied, _claude_log(WRITE)])
    errors = authoring_order_errors("claude", log, workspace, workspace, "workspace", True)
    assert errors == []


def test_order_check_reads_multiline_codex_commands_and_patches(tmp_path):
    workspace = tmp_path / "shop-e2e"

    def errors(log):
        return authoring_order_errors("codex", log, workspace, workspace, "business", True)

    script = (
        "exec\n/bin/bash -lc \"uv run --locked python - <<'PY'\n"
        "from pathlib import Path\nPath('tests/shop/case_a.yaml').write_text('x')\nPY\" in .\n"
        " succeeded in 3ms:\n"
    )
    doctor = _codex_command("uv run --locked apitest workspace doctor", PASSED)
    assert errors(script + doctor) == ["workspace doctor did not pass before the first write"]
    for patch_path in (f"{workspace}/coverage/shop.yaml", "coverage/shop.yaml"):
        patch = f"apply patch\npatch: completed\n{patch_path}\ndiff --git a b\n"
        assert errors(doctor + patch) == []
        assert errors(patch + doctor), patch_path
    failed_patch = "apply patch\npatch: failed\ncoverage/shop.yaml\n"
    assert errors(failed_patch + doctor + script) == []
    assert errors(_codex_command("uv run --locked apitest workspace doctor", FAILED) + script)


def test_timed_order_check_compares_doctor_and_locator_with_the_first_tree_change(tmp_path):
    import json

    workspace = tmp_path / "shop-e2e"
    lines = _claude_log(LOCATE, DOCTOR).splitlines()
    timeline = [(10.0 + i, "stdout", line) for i, line in enumerate(lines)]
    doctor_done = timeline[3][0]
    assert json.loads(timeline[3][2])["type"] == "user"

    def errors(changed_at):
        return authoring_order_errors(
            "claude", timeline, workspace, workspace, "workspace", True, changed_at
        )

    # Any write, however it was made, counts through the files' modification times.
    assert errors(doctor_done + 5) == []
    assert errors(doctor_done - 0.5) == ["workspace doctor did not pass before the first write"]
    assert errors(timeline[0][0] - 1) == [
        "workspace doctor did not pass before the first write",
        "the entry locator did not run before the first write",
    ]
    assert errors(None) == []


LATE = ["workspace doctor did not pass before the first write"]


@pytest.mark.parametrize(
    "steps, expected",
    [
        (["doctor", "case"], []),
        (["doctor", "fragment", "case", "fragment"], []),
        (["case", "doctor"], LATE),
        # The skill writes fragments first and updates them later: the first write counts.
        (["fragment", "doctor", "fragment", "case"], LATE),
        (["delete", "doctor", "case"], LATE),
    ],
)
def test_timed_order_check_end_to_end_with_a_scripted_host(tmp_path, steps, expected):
    import sys
    import textwrap

    workspace = tmp_path / "shop-e2e"
    for name in ("tests", "coverage"):
        (workspace / name).mkdir(parents=True)
    (workspace / "tests/old.yaml").write_text("manual")
    roots = (workspace / "tests", workspace / "coverage")
    before = [snapshot(root) for root in roots]
    host = tmp_path / "host.py"
    host.write_text(
        textwrap.dedent(
            f"""
            import json, sys, time
            from pathlib import Path

            workspace = Path({str(workspace)!r})

            def doctor():
                call = {{"type": "tool_use", "id": "d", "name": "Bash",
                        "input": {{"command": "uv run --locked apitest workspace doctor"}}}}
                result = {{"type": "tool_result", "tool_use_id": "d", "content": {PASSED!r}}}
                for kind, item in (("assistant", call), ("user", result)):
                    print(json.dumps({{"type": kind, "message": {{"content": [item]}}}}))
                sys.stdout.flush()

            # Generator-style writes: the transcript never names the files.
            steps = {{
                "doctor": doctor,
                "case": lambda: (workspace / "tests/case_a.yaml").write_text("x"),
                "fragment": lambda: (workspace / "coverage/x.yaml").write_text(str(time.time())),
                "delete": lambda: (workspace / "tests/old.yaml").unlink(),
            }}
            for name in {steps!r}:
                steps[name]()
                time.sleep(0.3)
            """
        )
    )
    code, timeline, first_seen = run_host(
        [sys.executable, str(host)], tmp_path, None, 30, watch=roots
    )
    assert code == 0 and timeline
    changed_at = min(t for t in (first_seen, tree_changed_at(workspace, before)) if t is not None)
    errors = authoring_order_errors(
        "claude", timeline, workspace, workspace, "business", True, changed_at
    )
    assert errors == expected


def test_run_host_keeps_output_and_ends_the_whole_host_on_timeout(tmp_path):
    import sys
    import time

    # A wrapper whose child keeps the output pipes open, as the codex launcher does.
    script = (
        "import subprocess, sys, time; print('started', flush=True); "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"
    )
    began = time.monotonic()
    code, timeline, _ = run_host([sys.executable, "-c", script], tmp_path, None, 1)
    assert code == 124 and time.monotonic() - began < 10
    assert [(stream, line) for _, stream, line in timeline] == [("stdout", "started")]


def _gone(pid):
    """The process has exited: absent, a zombie, or reaped while being checked."""
    import os

    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1].startswith("Z")
    except (ProcessLookupError, FileNotFoundError):
        return True


def _wait_until_gone(pids, deadline):
    import time

    while not all(_gone(int(pid)) for pid in pids.read_text().split()):
        assert time.monotonic() < deadline
        time.sleep(0.05)


def _host_script(pids):
    """A host that starts a child and records both pids."""
    return (
        "import os, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}'); time.sleep(30)"
    )


def test_run_host_stops_the_host_and_its_children_when_interrupted(tmp_path):
    import _thread
    import sys
    import threading
    import time

    pids = tmp_path / "pids"
    timer = threading.Timer(1.0, _thread.interrupt_main)
    timer.start()
    with pytest.raises(KeyboardInterrupt):
        run_host([sys.executable, "-c", _host_script(pids)], tmp_path, None, 60)
    _wait_until_gone(pids, time.monotonic() + 5)


def _start_driver(tmp_path, **popen):
    """A probe stand-in in its own process group (as from a terminal) that runs a host
    through run_host; its cleanup takes a moment so a repeated signal lands during it."""
    import subprocess
    import sys
    import textwrap
    import time

    pids, cleaned = tmp_path / "pids", tmp_path / "cleaned"
    driver = tmp_path / "driver.py"
    driver.write_text(
        textwrap.dedent(
            f"""
            import sys, time
            from pathlib import Path
            sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})
            from scripts.verify_host_authoring import exit_on_termination_signals, run_host

            exit_on_termination_signals()
            try:
                run_host(
                    [sys.executable, "-c", {_host_script(pids)!r}], {str(tmp_path)!r}, None, 60
                )
            finally:
                time.sleep(0.5)
                Path({str(cleaned)!r}).touch()
            """
        )
    )
    probe = subprocess.Popen([sys.executable, str(driver)], start_new_session=True, **popen)
    deadline = time.monotonic() + 10
    while not pids.exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return probe, pids, cleaned, deadline


@pytest.mark.parametrize("signum", ["SIGTERM", "SIGHUP"])
def test_termination_signals_stop_the_host_and_let_cleanup_finish(tmp_path, signum):
    import os
    import signal
    import time

    probe, pids, cleaned, deadline = _start_driver(tmp_path)
    number = getattr(signal, signum)
    os.killpg(probe.pid, number)
    # A closed terminal sends SIGHUP twice; the repeat must not cut the cleanup short.
    time.sleep(0.2)
    os.killpg(probe.pid, number)
    assert probe.wait(timeout=10) == 128 + number
    assert cleaned.exists()
    _wait_until_gone(pids, deadline)


def test_a_probe_started_under_nohup_ignores_hangups(tmp_path):
    import os
    import signal
    import time

    probe, pids, cleaned, deadline = _start_driver(
        tmp_path, preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN)
    )
    os.killpg(probe.pid, signal.SIGHUP)
    time.sleep(0.5)
    assert probe.poll() is None and not cleaned.exists()
    os.killpg(probe.pid, signal.SIGTERM)
    assert probe.wait(timeout=10) == 128 + signal.SIGTERM
    assert cleaned.exists()
    _wait_until_gone(pids, deadline)


def test_transcript_order_check_treats_scripts_and_tree_cd_as_writes(tmp_path):
    workspace = tmp_path / "shop-e2e"

    def errors(*calls):
        log = _claude_log(*calls)
        return authoring_order_errors("claude", log, workspace, workspace, "business", True)

    for write in (
        ("Bash", {"command": "uv run --locked python /tmp/gen.py"}),
        ("Bash", {"command": "bash scripts/make_cases.sh"}),
        ("Bash", {"command": "cd shop-e2e/tests/shop"}),
    ):
        assert errors(write, DOCTOR, WRITE) == [
            "workspace doctor did not pass before the first write"
        ], write
    assert errors(LOCATE, DOCTOR, WRITE) == []


def _extended_workspace(tmp_path, refund_status, promo_conditions, defect_record):
    import yaml

    source = "a" * 40
    (tmp_path / "coverage").mkdir()
    (tmp_path / "defects").mkdir()
    (tmp_path / "tests/shop/refunds").mkdir(parents=True)
    for path, conditions in (
        ("/api/promo", promo_conditions),
        ("/actuator/release", []),
        ("/actuator/health", []),
        ("/api/refunds/{id}", []),
    ):
        slug = path.strip("/").replace("/", "-").replace("{", "").replace("}", "")
        endpoint = {
            "service": "shop", "method": "GET", "path": path, "conditions": conditions,
            "rows": [{"key": f"shop|GET|{path}|happy|x", "source_commit": source}],
        }
        (tmp_path / f"coverage/{slug}.yaml").write_text(yaml.safe_dump(endpoint))
    case = {
        "schema": "v1", "name": "missing--abc123", "service": "shop",
        "steps": [{"name": "missing", "request": {"method": "GET", "path": "/api/refunds/0"},
                   "assert": {"status": refund_status}}],
    }
    (tmp_path / "tests/shop/refunds/case_missing--abc123.yaml").write_text(yaml.safe_dump(case))
    if defect_record:
        (tmp_path / "defects/refund-missing--abc123.md").write_text(defect_record)
    return tmp_path


REFUND_RECORD = "Suspected defect: GET /api/refunds/0 returns 200; the contract requires 404.\n"


def test_extended_fixture_accepts_contract_assertions_and_recorded_conditions(tmp_path):
    workspace = _extended_workspace(tmp_path, 404, ["shop.promo.enabled=true"], REFUND_RECORD)
    assert extended_fixture_errors(workspace) == []


NO_NOTE = "no defects/*.md note records the refund lookup conflict; check the final report manually"


def test_extended_fixture_rejects_asserting_the_conflicting_implementation(tmp_path):
    workspace = _extended_workspace(tmp_path, 200, [], False)
    errors = extended_fixture_errors(workspace)
    assert any("asserts the implementation's 200" in e for e in errors)
    assert any("no enabling condition" in e for e in errors)
    assert NO_NOTE in errors


@pytest.mark.parametrize(
    "note",
    [
        "# Suspected: /api/refunds/{id} returns 200 instead of 404 for unknown ids\n",
        "# 不存在的退款返回 200，与 404 契约冲突\n\n实现依据：Refunds.java:3–5\n",
        "Suspected defect: GET /api/refunds/0 returns 200; the contract requires 404.\n",
    ],
)
def test_a_refund_lookup_defect_note_certifies_the_conflict(tmp_path, note):
    workspace = _extended_workspace(tmp_path, 404, ["shop.promo.enabled=true"], note)
    assert extended_fixture_errors(workspace) == []


@pytest.mark.parametrize(
    "note",
    [
        None,
        "Suspected defect: /api/health returns text instead of JSON.\n",
        "Suspected: /api/refunds/quote accepts 0 (200) where the contract says 400 or 404.\n",
        "Suspected: refund idempotency keys are ignored (200 instead of 404).\n",
    ],
)
def test_only_a_refund_lookup_note_certifies_the_conflict(tmp_path, note):
    # A conflict stated only in the final report is left for manual confirmation.
    workspace = _extended_workspace(tmp_path, 404, ["shop.promo.enabled=true"], note)
    assert extended_fixture_errors(workspace) == [NO_NOTE]


def test_reevaluate_rebases_moved_artifacts_and_keeps_probe_json(tmp_path):
    import json

    artifacts = tmp_path / "moved"
    workspace = artifacts / "shop-e2e"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "repo2test.toml").write_text('format = 1\n[target]\npath = "../shop"\n')
    old = "/elsewhere/codex-natural-sandbox-x"
    probe = {
        "host": "codex",
        "scenario": "sandbox",
        "start_in": "business",
        "scope": "whole",
        "artifacts": old,
        "passed": True,
        "cases": 0,
    }
    (artifacts / "probe.json").write_text(json.dumps(probe))
    (artifacts / "final.log").write_text("")
    patch = f"apply patch\npatch: completed\n{old}/shop-e2e/tests/case_a.yaml\n\n"
    doctor = _codex_command("uv run --locked apitest workspace doctor", PASSED)
    (artifacts / "host.log").write_text(patch + doctor)
    original = (artifacts / "probe.json").read_bytes()
    record = reevaluate(artifacts)
    # The write is found at its rebased path, so the late doctor is reported.
    assert record["authoring_order_errors"] == [
        "workspace doctor did not pass before the first write"
    ]
    assert "paths_match" not in record
    assert (artifacts / "probe.json").read_bytes() == original
    assert list(artifacts.glob(f"reevaluation-{record['oracle_commit'][:12]}*.json"))


def test_extended_inventory_may_include_the_actuator_discovery_page():
    expected = expected_paths("whole", False, True)
    listed = {*EXTENDED_PATHS, "/api/health", "/api/refunds/quote", "/api/refunds/{id}"}
    assert comparable_paths(listed | {"/actuator"}, True) == expected
    assert comparable_paths(listed, True) == expected
    assert comparable_paths(listed - {"/actuator/health"}, True) != expected
