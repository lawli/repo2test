"""Five-phase case orchestrator: LOAD -> SETUP -> EXECUTE -> VERIFY -> TEARDOWN.

Wires the HTTP client, DB / Redis / RabbitMQ fixtures and Python helpers around
a case's steps. Teardown always runs, whichever phase failed.
"""

from __future__ import annotations

import contextlib
import enum
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from jinja2 import UndefinedError

from apitest.case_id import IdSpec, allocate_case_id, allocate_ids
from apitest.ctx import Case as CaseObj
from apitest.ctx import Ctx
from apitest.escape import DiContainer, HelperResolver, invoke_helper
from apitest.exceptions import VerifyError
from apitest.fixtures.db import DbClient, run_phase_sql
from apitest.fixtures.rabbitmq import CapturedMessage, MqClient
from apitest.fixtures.readonly_sql import check_verification_sql
from apitest.fixtures.redis import RedisClient
from apitest.http_client import NO_RESPONSE_EXCEPTIONS, HttpClient, merge_headers
from apitest.preflight import prepare_profile
from apitest.profile import Profile
from apitest.runner.assertions import AssertionMismatch
from apitest.runner.steps import execute_step
from apitest.schema.case_v1 import Case as CaseModel
from apitest.schema.case_v1 import (
    DbVerify,
    IdSpecModel,
    MqSnifferVerify,
    RedisVerify,
    WaitSpec,
)
from apitest.taint import redact
from apitest.templating import ctx_references, render_string, render_value


class CaseOutcome(enum.StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"
    XFAIL = "XFAIL"
    XPASS = "XPASS"


@dataclass
class CaseResult:
    outcome: CaseOutcome
    error_message: str | None = None
    teardown_ran: bool = False
    teardown_errors: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    http_log: list[dict[str, Any]] = field(default_factory=list)  # redacted exchanges
    business_outcome: str | None = None
    failed_step: str | None = None
    failed_at: str | None = None
    residual_data: dict[str, Any] = field(default_factory=dict)
    cleanup_guidance: str | None = None


def _to_idspec(d: dict[str, IdSpecModel]) -> dict[str, IdSpec]:
    return {n: IdSpec(kind=v.kind, prefix=v.prefix) for n, v in d.items()}


def run_case(
    case_model: CaseModel,
    *,
    profile: Profile,
    scope_dir: Path | None,
    helper_resolver: HelperResolver | None = None,
    case_yaml_path: Path | None = None,
) -> CaseResult:
    timings: dict[str, float] = {}
    teardown_errors: list[str] = []
    residual_resources: dict[str, Any] = {}
    cleanup_hints: list[str] = []
    profile, blockers = prepare_profile(case_model, profile, scope_dir=scope_dir)
    if blockers:
        return CaseResult(outcome=CaseOutcome.BLOCKED, error_message=redact("; ".join(blockers)))

    # ── 1. LOAD ──
    t0 = time.monotonic()
    full, short = allocate_case_id()
    ids = allocate_ids(_to_idspec(case_model.ids), case_id_short=short)
    case_obj = CaseObj(id=full, id_short=short)
    for k, v in ids.items():
        setattr(case_obj, k, v)
    ctx = Ctx(case=case_obj, profile=profile)
    try:
        http = HttpClient(profile)
    except Exception as exc:
        return CaseResult(outcome=CaseOutcome.ERROR, error_message=redact(f"LOAD failed: {exc}"))
    ctx.bind_client("http", http)

    db_client = None
    redis_client = None
    mq_client = None
    try:
        db_client = DbClient(profile) if profile.databases else None
        redis_client = RedisClient(profile, case_id_short=short) if profile.redis else None
        mq_client = MqClient(profile, case_id_short=short) if profile.rabbitmq else None
        db_handle = (
            db_client.for_service(case_model.service)
            if db_client and case_model.service in profile.databases
            else None
        )
    except Exception as exc:
        _close_quietly(mq_client, redis_client, db_client, http)
        return CaseResult(outcome=CaseOutcome.ERROR, error_message=redact(f"LOAD failed: {exc}"))
    if db_handle is not None:
        ctx.bind_client("db", db_handle)
    if redis_client:
        ctx.bind_client("redis", redis_client)
    if mq_client:
        ctx.bind_client("mq", mq_client)
    timings["load"] = time.monotonic() - t0

    di = DiContainer(
        http=http,
        case=case_obj,
        profile=profile,
        ctx=ctx,
        db=db_handle,
        redis=redis_client,
        mq=mq_client,
    )

    # ── 2. SETUP ──
    primary_outcome: CaseOutcome | None = None
    primary_error: str | None = None
    failed_step: str | None = None
    failed_at: str | None = None

    # Extract name -> steps that produce it, and steps whose request may have
    # reached the service. Cleanup that only depends on never-requested
    # resources is skipped rather than reported as a cleanup failure.
    producers: dict[str, set[str]] = {}
    for producing in case_model.setup.steps + case_model.steps:
        for extract_name in producing.extract:
            producers.setdefault(extract_name, set()).add(producing.name)
    reached: set[str] = set()
    answered: dict[str, int | None] = {}  # final status of each step in `reached`

    def base_headers(step: Any) -> dict[str, str]:
        target = step.service or case_model.service
        base = case_model.service_headers.get(target, {}) if step.role is None else {}
        if target == case_model.service and step.role is None:
            base = {**case_model.headers, **base}
        return base

    setup_helper_started = False

    def run_step(step: Any, *, cleanup: bool = False) -> None:
        before = http.delivered_count
        try:
            execute_step(
                step,
                ctx=ctx,
                http=http,
                service=case_model.service,
                scope_dir=scope_dir,
                base_headers=base_headers(step),
                cleanup=cleanup,
            )
        finally:
            if http.delivered_count > before:
                reached.add(step.name)
                answered[step.name] = http.last_status

    def referenced(texts: list[Any]) -> set[str]:
        refs: set[str] = set()
        pending = list(texts)
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str):
                refs |= ctx_references(value)
        return refs

    def never_created(texts: list[Any]) -> str:
        """Why cleanup cannot apply: missing ctx names whose producing steps never
        reached the service. Empty when the cleanup must run."""
        if setup_helper_started:
            return ""  # A helper may create resources under any name.
        refs = referenced(texts)
        missing = sorted(r for r in refs if r not in ctx._extracts)
        if not missing or any(r not in producers or producers[r] & reached for r in missing):
            return ""
        reason = f"{', '.join(missing)} never created"
        if present := sorted(refs - set(missing)):
            # The skipped cleanup also named values that exist, possibly a created resource.
            reason += f"; it also references {', '.join(present)}: check for manual cleanup"
        return reason

    def unreturned(texts: list[Any]) -> str:
        """Which missing ctx names a step that reached the service failed to return."""
        parts = []
        for name in sorted(r for r in referenced(texts) if r not in ctx._extracts):
            steps = sorted(producers.get(name, set()) & reached)
            if steps:
                answers = ", ".join(
                    f"step {s!r} answered HTTP {answered[s]}"
                    if answered[s] is not None
                    else f"step {s!r} reached the service without a usable response"
                    for s in steps
                )
                parts.append(f"ctx.{name} was not returned ({answers})")
        return "; ".join(parts)

    def cleanup_inputs(step: Any) -> list[Any]:
        req = step.request
        texts: list[Any] = [
            req.path,
            req.params,
            req.body,
            req.form,
            req.raw,
            merge_headers(base_headers(step), req.headers),
        ]
        if req.body_template and scope_dir is not None:
            template = scope_dir / req.body_template
            if template.is_file():
                texts.append(template.read_text(errors="replace"))
        return texts

    skipped_cleanup: list[str] = []

    t0 = time.monotonic()
    try:
        for step in case_model.setup.steps:
            failed_step = step.name
            run_step(step)
        failed_step = "setup.db"
        if case_model.setup.db and db_client and scope_dir is not None:
            handle = db_client.for_service(case_model.service)
            files = [scope_dir / f for f in case_model.setup.db]
            run_phase_sql(handle, files, ctx_vars=_ctx_dict_full(ctx))

        failed_step = "setup.redis"
        if case_model.setup.redis and redis_client and scope_dir is not None:
            for rel in case_model.setup.redis:
                _apply_redis_setup(redis_client, scope_dir / rel, _ctx_dict_full(ctx))

        failed_step = "setup.rabbitmq"
        if case_model.setup.rabbitmq and mq_client:
            for _alias_unused, decls in case_model.setup.rabbitmq.items():
                for d in decls:
                    rk = render_string(d.routing_key, _ctx_dict_full(ctx))
                    mq_client.declare_sniffer(alias=d.as_, exchange=d.exchange, routing_key=rk)

        for call in case_model.setup.python:
            failed_step = f"setup helper {call.call}"
            setup_helper_started = True
            if helper_resolver is None or case_yaml_path is None:
                raise RuntimeError(f"helper resolver missing for setup call {call.call}")
            fn = helper_resolver.resolve(call.call, case_path=case_yaml_path)
            ret = invoke_helper(fn, args=dict(call.args), di=di)
            if isinstance(ret, dict):
                for k, v in ret.items():
                    if k not in ctx._extracts:
                        ctx.set_extract(k, v)
        failed_step = None
    except Exception as e:  # noqa: BLE001 - any setup failure is an ERROR; teardown still runs
        primary_outcome = CaseOutcome.ERROR
        primary_error = f"SETUP failed: {e}"
    timings["setup"] = time.monotonic() - t0

    # ── 3. EXECUTE ──
    if primary_outcome is None:
        t0 = time.monotonic()
        try:
            for step in case_model.steps:
                failed_step = step.name
                run_step(step)
            failed_step = None
        except AssertionMismatch as e:
            primary_outcome = CaseOutcome.FAIL
            primary_error = str(e)
            failed_at = e.at
        except Exception as e:  # noqa: BLE001 - any non-assertion exception is an ERROR outcome
            primary_outcome = CaseOutcome.ERROR
            primary_error = f"EXECUTE error: {e}"
            if isinstance(e, NO_RESPONSE_EXCEPTIONS):
                failed_at = "response"
        timings["execute"] = time.monotonic() - t0

    # ── 4. VERIFY ──
    if primary_outcome is None:
        t0 = time.monotonic()
        try:
            ctx_vars = _ctx_dict_full(ctx)
            if db_client and case_model.verify.db:
                handle = db_client.for_verification(case_model.service)
                for dbv in case_model.verify.db:
                    _wait_until(dbv.wait, partial(_check_db, handle, dbv, ctx_vars))

            if redis_client:
                for rv in case_model.verify.redis:
                    _wait_until(rv.wait, partial(_check_redis, redis_client, rv, ctx_vars))

            if mq_client:
                # Drained messages are gone from the broker, so keep everything
                # captured per sniffer for later entries / polls.
                captured: dict[str, list[CapturedMessage]] = {}
                for mv in case_model.verify.rabbitmq:
                    _wait_until(mv.wait, partial(_check_mq, mq_client, mv, captured, ctx_vars))

            for call in case_model.verify.python:
                if helper_resolver is None or case_yaml_path is None:
                    raise RuntimeError(f"helper resolver missing for verify call {call.call}")
                fn = helper_resolver.resolve(call.call, case_path=case_yaml_path)
                invoke_helper(fn, args=dict(call.args), di=di)
        except VerifyError as e:
            primary_outcome = CaseOutcome.FAIL
            primary_error = str(e)
        except Exception as e:  # noqa: BLE001 - any non-VerifyError exception is an ERROR outcome
            primary_outcome = CaseOutcome.ERROR
            primary_error = f"VERIFY error: {e}"
        timings["verify"] = time.monotonic() - t0

    # ── 5. TEARDOWN ──
    t0 = time.monotonic()
    teardown_ran = True
    try:
        for step in case_model.teardown.steps:
            inputs = cleanup_inputs(step)
            if reason := never_created(inputs):
                skipped_cleanup.append(f"cleanup step {step.name!r} skipped: {reason}")
                continue
            try:
                run_step(step, cleanup=True)
            except UndefinedError as exc:
                # The request could not be built. Say which step lost the value
                # instead of surfacing the template engine's wording.
                lost = unreturned(inputs)
                teardown_errors.append(
                    f"teardown step {step.name}: "
                    + (f"{lost}; the record may still exist" if lost else str(exc))
                )
            except Exception as exc:
                teardown_errors.append(f"teardown step {step.name}: {exc}")
        for call in case_model.teardown.python:
            try:
                if helper_resolver is None or case_yaml_path is None:
                    raise RuntimeError(f"helper resolver missing for teardown call {call.call}")
                fn = helper_resolver.resolve(call.call, case_path=case_yaml_path)
                invoke_helper(fn, args=dict(call.args), di=di)
            except Exception as e:  # noqa: BLE001 - record per-helper teardown errors but continue
                teardown_errors.append(f"teardown helper {call.call}: {e}")

        if mq_client:
            try:
                mq_client.close()
            except Exception as e:  # noqa: BLE001 — close failures shouldn't override outcome
                teardown_errors.append(f"mq close: {e}")
                residual_resources["rabbitmq_queues"] = mq_client.pending_queues()
                cleanup_hints.append(
                    "RabbitMQ queue deletion is unconfirmed. Check the listed exclusive queues "
                    "on the broker and close their owning connection if it remains open."
                )

        if redis_client:
            try:
                redis_client.cleanup()
            except Exception as e:  # noqa: BLE001
                teardown_errors.append(f"redis cleanup: {e}")
            for raw_key in case_model.teardown.redis.delete:
                if reason := never_created([raw_key]):
                    skipped_cleanup.append(f"redis key cleanup skipped: {reason}")
                    continue
                try:
                    redis_client.delete_unprefixed(render_string(raw_key, _ctx_dict_full(ctx)))
                except Exception as e:  # noqa: BLE001
                    teardown_errors.append(f"redis key cleanup: {e}")
            try:
                redis_client.close()
            except Exception as e:  # noqa: BLE001
                teardown_errors.append(f"redis close: {e}")

        if case_model.teardown.db and db_client and scope_dir is not None:
            files = [scope_dir / f for f in case_model.teardown.db]
            try:
                handle = db_client.for_service(case_model.service)
                run_phase_sql(handle, files, ctx_vars=_ctx_dict_full(ctx))
            except Exception as e:  # noqa: BLE001
                teardown_errors.append(f"db cleanup: {e}")
    finally:
        for label, close in (
            ("db", db_client.close_all if db_client is not None else None),
            ("http", http.close),
        ):
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    teardown_errors.append(f"{label} close: {exc}")
    timings["teardown"] = time.monotonic() - t0

    if primary_outcome is None:
        primary_outcome = CaseOutcome.PASS

    business_outcome = primary_outcome.value
    markers = [d for d in case_model.known_defects if d.env in (None, profile.name)]
    if (
        primary_outcome is CaseOutcome.FAIL
        and failed_at is not None
        and any(d.step == failed_step and (d.at is None or d.at == failed_at) for d in markers)
    ) or (
        # A dropped connection is no assertion failure: only a marker that names it matches.
        primary_outcome is CaseOutcome.ERROR
        and failed_at == "response"
        and any(d.step == failed_step and d.at == "response" for d in markers)
    ):
        primary_outcome = CaseOutcome.XFAIL
    elif primary_outcome is CaseOutcome.PASS and markers:
        primary_outcome = CaseOutcome.XPASS
        primary_error = "XPASS: marked business expectations passed; review known_defects"
    if skipped_cleanup:
        primary_error = "; ".join([*([primary_error] if primary_error else []), *skipped_cleanup])
    if teardown_errors:
        primary_outcome = CaseOutcome.ERROR
        primary_error = (primary_error + "; " if primary_error else "") + "cleanup failed"

    return CaseResult(
        outcome=primary_outcome,
        error_message=redact(primary_error) if primary_error is not None else None,
        teardown_ran=teardown_ran,
        teardown_errors=[redact(t) for t in teardown_errors],
        timings=timings,
        http_log=redact(http.drain_log()),
        business_outcome=business_outcome,
        failed_step=failed_step,
        failed_at=failed_at,
        residual_data=redact(
            {
                "case": vars(case_obj),
                "extracts": ctx._extracts,
                "resources": case_model.data.resources,
                **residual_resources,
            }
        )
        if teardown_errors
        else {},
        cleanup_guidance=redact(" ".join([case_model.data.cleanup, *cleanup_hints]).strip())
        if teardown_errors
        else None,
    )


def _wait_until(wait: WaitSpec | None, check: Callable[[], None]) -> None:
    """Run `check` (raises VerifyError on mismatch); with a WaitSpec, retry
    every `interval_s` until it passes or `timeout_s` elapses."""
    if wait is None:
        check()
        return
    deadline = time.monotonic() + wait.timeout_s
    while True:
        try:
            check()
            return
        except VerifyError as e:
            if time.monotonic() >= deadline:
                raise VerifyError(f"{e} (after waiting {wait.timeout_s}s)") from None
            time.sleep(wait.interval_s)


def _check_db(handle: Any, dbv: DbVerify, ctx_vars: dict[str, Any]) -> None:
    sql = render_string(dbv.sql, ctx_vars)
    check_verification_sql(sql)
    rows = handle.query(sql)
    if dbv.expect_count is not None and len(rows) != dbv.expect_count:
        raise VerifyError(
            f"db verify count: expected {dbv.expect_count}, got {len(rows)} for {sql!r}"
        )
    if dbv.expect_rows is not None:
        rendered_expects = [
            {k: render_value(v, ctx_vars) if isinstance(v, str) else v for k, v in exp.items()}
            for exp in dbv.expect_rows
        ]
        if not all(any(_row_matches(r, exp) for r in rows) for exp in rendered_expects):
            raise VerifyError(
                f"db verify rows mismatch for {sql!r}: rows={rows}, expected={rendered_expects}"
            )


def _check_redis(rc: RedisClient, rv: RedisVerify, ctx_vars: dict[str, Any]) -> None:
    key = render_string(rv.key, ctx_vars)
    if rv.exists is not None and rv.exists != rc.exists(key, _allow_unprefixed=rv.unprefixed):
        raise VerifyError(f"redis exists mismatch for {key}")
    if rv.value is not None:
        actual = rc.get(key, _allow_unprefixed=rv.unprefixed)
        expected_value = render_value(rv.value, ctx_vars) if isinstance(rv.value, str) else rv.value
        # redis values are strings on the wire; compare as such
        if actual is None or str(actual) != str(expected_value):
            raise VerifyError(f"redis value mismatch for {key}: {actual!r} != {expected_value!r}")
    if rv.ttl_between:
        t = rc.ttl(key, _allow_unprefixed=rv.unprefixed)
        lo, hi = rv.ttl_between
        if not (lo <= t <= hi):
            raise VerifyError(f"redis ttl {t} not in [{lo},{hi}] for {key}")


def _check_mq(
    mq: MqClient,
    mv: MqSnifferVerify,
    captured: dict[str, list[CapturedMessage]],
    ctx_vars: dict[str, Any],
) -> None:
    msgs = captured.setdefault(mv.sniffer, [])
    msgs.extend(mq.drain(mv.sniffer))
    matched = [m for m in msgs if _mq_match(m, mv.match or {}, ctx_vars)]
    n = len(matched)
    if mv.count is not None and n != mv.count:
        raise VerifyError(f"mq sniffer {mv.sniffer} count: expected {mv.count}, got {n}")
    if mv.count_min is not None and n < mv.count_min:
        raise VerifyError(
            f"mq sniffer {mv.sniffer} count: expected at least {mv.count_min}, got {n}"
        )
    if mv.count_max is not None and n > mv.count_max:
        raise VerifyError(
            f"mq sniffer {mv.sniffer} count: expected at most {mv.count_max}, got {n}"
        )


def _ctx_dict_full(ctx: Ctx) -> dict[str, Any]:
    case = ctx.case
    case_d = {k: getattr(case, k) for k in vars(case)}
    profile_d: dict[str, Any] = {}
    if ctx.profile is not None:
        profile_d = {
            "test_data": getattr(ctx.profile, "test_data", {}) or {},
        }
    return {
        "ctx": {
            "case": case_d,
            "profile": profile_d,
            **ctx._extracts,
        }
    }


def _apply_redis_setup(rc: RedisClient, path: Path, ctx_vars: dict[str, Any]) -> None:
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    for entry in data.get("set", []) or []:
        rc.set(
            render_string(entry["key"], ctx_vars),
            render_string(str(entry["value"]), ctx_vars),
            ttl=entry.get("ttl"),
            unprefixed=bool(entry.get("unprefixed", False)),
        )
    for entry in data.get("hset", []) or []:
        rc.hset(
            render_string(entry["key"], ctx_vars),
            entry["fields"],
            ttl=entry.get("ttl"),
            unprefixed=bool(entry.get("unprefixed", False)),
        )


def _values_equal(actual: Any, expected: Any) -> bool:
    """DB drivers return Decimal/datetime/int; YAML gives str/float/bool. Fall
    back to a string comparison when the native comparison fails."""
    return bool(actual == expected) or str(actual) == str(expected)


def _row_matches(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(_values_equal(row.get(k), v) for k, v in expected.items())


def _mq_match(msg: Any, expected: dict[str, Any], ctx_vars: dict[str, Any]) -> bool:
    body = msg.body_json
    headers = msg.headers
    for path, exp in expected.items():
        rendered_exp = render_string(str(exp), ctx_vars) if isinstance(exp, str) else exp
        actual: Any = None
        if path.startswith("body."):
            cur: Any = body
            for p in path[5:].split("."):
                cur = cur.get(p) if isinstance(cur, dict) else None
            actual = cur
        elif path.startswith("headers."):
            actual = headers.get(path[8:])
        if str(actual) != str(rendered_exp):
            return False
    return True


def _close_quietly(*objs: Any) -> None:
    for obj in objs:
        if obj is None:
            continue
        with contextlib.suppress(Exception):
            if hasattr(obj, "close_all"):
                obj.close_all()
            else:
                obj.close()
