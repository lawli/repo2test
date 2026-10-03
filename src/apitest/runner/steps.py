"""Step execution: render -> call -> extract -> assert."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apitest.ctx import Ctx
from apitest.http_client import HttpClient, merge_headers
from apitest.runner.assertions import AssertionMismatch, assert_response
from apitest.runner.extract import extract_jsonpath
from apitest.schema.case_v1 import Step
from apitest.templating import render_string, render_template, render_value


def _ctx_dict(ctx: Ctx) -> dict[str, Any]:
    """Flatten ctx attributes for template rendering."""
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


def _render_inline(value: Any, ctx_vars: dict[str, Any]) -> Any:
    """Walk an inline body, rendering every string through the templating engine.

    A string that is exactly one `${expr}` keeps the expression's native type.
    """
    if isinstance(value, str):
        return render_value(value, ctx_vars)
    if isinstance(value, dict):
        return {k: _render_inline(v, ctx_vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_inline(v, ctx_vars) for v in value]
    return value


def _render_json_body(step: Step, ctx_vars: dict[str, Any], scope_dir: Path | None) -> Any:
    if step.request.body_template:
        if scope_dir is None:
            raise ValueError("body_template used but no scope_dir provided")
        text = render_template(
            scope_dir / step.request.body_template,
            {**ctx_vars, **step.request.body_vars},
        )
        return json.loads(text)
    if step.request.body is not None:
        return _render_inline(step.request.body, ctx_vars)
    return None


def _open_files(step: Step, scope_dir: Path | None) -> dict[str, tuple[str, bytes]]:
    if not step.request.files:
        return {}
    if scope_dir is None:
        raise ValueError("files used but no scope_dir provided")
    out: dict[str, tuple[str, bytes]] = {}
    for field, rel in step.request.files.items():
        path = scope_dir / rel
        out[field] = (path.name, path.read_bytes())
    return out


def execute_step(
    step: Step,
    *,
    ctx: Ctx,
    http: HttpClient,
    service: str,
    scope_dir: Path | None,
    base_headers: dict[str, str] | None = None,
    cleanup: bool = False,
) -> None:
    ctx_vars = _ctx_dict(ctx)
    req = step.request
    target_service = step.service or service
    path = render_string(req.path, ctx_vars)
    headers = {
        k: render_string(v, ctx_vars) for k, v in merge_headers(base_headers, req.headers).items()
    }
    params = _render_inline(req.params, ctx_vars) or None
    form = _render_inline(req.form, ctx_vars) if req.form is not None else None
    raw = render_string(req.raw, ctx_vars).encode("utf-8") if req.raw is not None else None
    files = _open_files(step, scope_dir) or None

    role_kw: dict[str, Any] = {"role": step.role} if step.role else {}
    r = http.request(
        req.method,
        target_service,
        path,
        headers=headers,
        params=params,
        json=_render_json_body(step, ctx_vars, scope_dir),
        data=form,
        files=files,
        content=raw,
        **role_kw,
    )

    body_json: Any
    try:
        body_json = r.json()
    except (ValueError, TypeError):
        body_json = None

    # Preserve available identifiers even if assertions fail, so teardown can
    # remove resources created by a response that violates its contract.
    missing_extracts = []
    for name, jpath in step.extract.items():
        try:
            ctx.set_extract(name, extract_jsonpath(body_json, jpath))
        except KeyError:
            missing_extracts.append((name, jpath))

    # Assertion failures take precedence over missing fields in an error body.
    a = step.assert_
    if a.status is not None or a.json_ or a.headers or a.text is not None:
        assert_response(
            status=r.status_code,
            body=body_json if body_json is not None else {},
            headers=r.headers,
            text=r.text,
            expected={
                "status": a.status,
                "json": _render_inline(a.json_, ctx_vars),
                "headers": _render_inline(a.headers, ctx_vars),
                "text": _render_inline(a.text, ctx_vars),
            },
        )
    if cleanup and a.status is None:
        r.raise_for_status()

    if missing_extracts:
        name, jpath = missing_extracts[0]
        raise AssertionMismatch(
            f"extract {name!r}: jsonpath {jpath} not found in response "
            f"(status {r.status_code}, body {r.text[:300]!r})",
            at=f"extract.{name}",
        )
