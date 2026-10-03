"""Resolve only a selected case's dependencies, without making network calls."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jinja2 import TemplateSyntaxError

from apitest.fixtures.readonly_sql import check_verification_sql
from apitest.http_client import merge_headers
from apitest.profile import Profile, ProfileError, _substitute_env
from apitest.schema.case_v1 import Case
from apitest.templating import template_dependencies


def case_services(case: Case) -> set[str]:
    return {
        case.service,
        *case.requires.services,
        *(s.service or case.service for s in case.setup.steps + case.steps + case.teardown.steps),
    }


READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def helper_calls(case: Case) -> list[str]:
    return [c.call for c in case.setup.python + case.verify.python + case.teardown.python]


def write_reasons(case: Case) -> list[str]:
    """Why a case must be treated as mutating data; empty for a read-only case.

    Python helpers are arbitrary code, so they count as writes unless the case
    declares `mutates: false`. That declaration waives the write prerequisites
    (isolation, cleanup, teardown), never the non-production requirement that
    `helper_calls` enforces separately.
    """
    reasons = ["mutates: true"] if case.mutates is True else []
    reasons += [
        label
        for label, present in (
            ("setup.db", case.setup.db),
            ("setup.redis", case.setup.redis),
            ("setup.rabbitmq", case.setup.rabbitmq),
            ("teardown.db", case.teardown.db),
            ("teardown.redis.delete", case.teardown.redis.delete),
        )
        if present
    ]
    for phase, steps in (("setup", case.setup.steps), ("teardown", case.teardown.steps)):
        reasons += [
            f"{phase} step {s.name!r} uses {s.request.method}"
            for s in steps
            if s.request.method not in READ_METHODS
        ]
    reasons += [
        f"step {s.name!r} uses {s.request.method}"
        for s in case.steps
        if s.request.method in {"PUT", "PATCH", "DELETE"}
        or (case.mutates is not False and s.request.method not in READ_METHODS)
    ]
    if case.mutates is not False:
        reasons += [f"Python helper {call}" for call in helper_calls(case)]
    return reasons


def is_write(case: Case) -> bool:
    return bool(write_reasons(case))


def fixture_paths(case: Case) -> list[str]:
    paths = [*case.setup.db, *case.setup.redis, *case.teardown.db]
    for step in case.setup.steps + case.steps + case.teardown.steps:
        paths.extend(step.request.files.values())
        if step.request.body_template:
            paths.append(step.request.body_template)
    return paths


def test_data_keys(case: Case, scope_dir: Path | None = None) -> set[str]:
    keys = set(case.requires.test_data)
    dynamic = False

    def inspect(value: Any) -> list[str]:
        nonlocal dynamic
        if isinstance(value, dict):
            return [p for item in value.values() for p in inspect(item)]
        if isinstance(value, list):
            return [p for item in value for p in inspect(item)]
        if not isinstance(value, str) or not any(tag in value for tag in ("${", "{{", "{%")):
            return []
        try:
            found, indirect, includes = template_dependencies(value)
        except TemplateSyntaxError as exc:
            raise ValueError(f"invalid dependency template: {exc}") from exc
        keys.update(found)
        dynamic |= indirect
        return includes

    # Match the renderer: metadata, helper args, extract paths, body_vars and
    # uploaded bytes are literal inputs, not templates.
    templates = [*case.setup.db, *case.teardown.db]
    for step in case.setup.steps + case.steps + case.teardown.steps:
        request = step.request
        target = step.service or case.service
        headers = case.service_headers.get(target, {}) if step.role is None else {}
        if target == case.service and step.role is None:
            headers = {**case.headers, **headers}
        inspect(merge_headers(headers, request.headers))
        inspect([request.path, request.params, request.body, request.form, request.raw])
        inspect([step.assert_.json_, step.assert_.headers, step.assert_.text])
        if request.body_template:
            templates.append(request.body_template)
    for declarations in case.setup.rabbitmq.values():
        inspect([decl.routing_key for decl in declarations])
    for db in case.verify.db:
        inspect(db.sql)
        for row in db.expect_rows or []:
            inspect([v for v in row.values() if isinstance(v, str)])
    for redis in case.verify.redis:
        inspect([redis.key, redis.value])
    for mq in case.verify.rabbitmq:
        inspect([v for v in (mq.match or {}).values() if isinstance(v, str)])
    inspect(case.teardown.redis.delete)
    if scope_dir:
        for rel in case.setup.redis:
            path = scope_dir / rel
            if path.is_file():
                data = yaml.safe_load(path.read_text()) or {}
                for entry in data.get("set", []) or []:
                    inspect([entry["key"], str(entry["value"])])
                for entry in data.get("hset", []) or []:
                    inspect(entry["key"])
        for rel in templates:
            path = scope_dir / rel
            pending = [path]
            visited: set[Path] = set()
            while pending:
                current = pending.pop().resolve()
                if current in visited or not current.is_file():
                    continue
                visited.add(current)
                for included in inspect(current.read_text(errors="replace")):
                    included_path = (path.parent / included).resolve()
                    if not included_path.is_relative_to(path.parent.resolve()):
                        raise ValueError(f"template include escapes fixture directory: {included}")
                    if not included_path.is_file():
                        raise ValueError(f"template include missing: {included}")
                    pending.append(included_path)
    if dynamic and not case.requires.test_data:
        raise ValueError("dynamic test_data access or template include requires requires.test_data")
    return keys


def prepare_profile(
    case: Case, profile: Profile, *, scope_dir: Path | None = None
) -> tuple[Profile, list[str]]:
    missing: list[str] = []
    for verify in case.verify.db:
        try:
            check_verification_sql(verify.sql, template=True)
        except ValueError as exc:
            missing.append(str(exc))

    def resolved(value: Any, label: str) -> Any:
        try:
            return _substitute_env(value)
        except ProfileError as exc:
            missing.append(f"{label}: {exc}")
            return {}

    services = {}
    steps = case.setup.steps + case.steps + case.teardown.steps
    for fixture in fixture_paths(case):
        if scope_dir is None or not (scope_dir / fixture).is_file():
            missing.append(f"fixture {fixture}: file required")
    for name in sorted(case_services(case)):
        config = dict(profile.services.get(name) or {})
        roles = config.pop("roles", {})
        selected_roles = {s.role for s in steps if (s.service or case.service) == name and s.role}
        uses_default = name in case.requires.services or any(
            (s.service or case.service) == name and s.role is None for s in steps
        )
        if not uses_default:
            config.pop("headers", None)
        svc = resolved(config, f"service {name}")
        svc["roles"] = {}
        for role in sorted(selected_roles):
            if role not in roles:
                missing.append(f"service {name}: role {role!r} is not configured")
            else:
                svc["roles"][role] = resolved(roles[role], f"service {name} role {role}")
        url = str(svc.get("base_url", ""))
        if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            missing.append(f"service {name}: a valid HTTP service URL is required")
        services[name] = svc
    db_names = set(case.requires.databases)
    if case.setup.db or case.verify.db or case.teardown.db:
        db_names.add(case.service)
    databases = {}
    for name in sorted(db_names):
        if name not in profile.databases:
            missing.append(f"database {name}: configuration required")
        else:
            databases[name] = resolved(profile.databases[name], f"database {name}")
            if not databases[name].get("dsn"):
                missing.append(f"database {name}: dsn required")
    needs_redis = bool(
        case.requires.redis or case.setup.redis or case.verify.redis or case.teardown.redis.delete
    )
    needs_mq = bool(case.requires.rabbitmq or case.setup.rabbitmq or case.verify.rabbitmq)
    redis = resolved({"default": profile.redis.get("default", {})}, "redis") if needs_redis else {}
    rabbitmq = (
        resolved({"default": profile.rabbitmq.get("default", {})}, "rabbitmq") if needs_mq else {}
    )
    if needs_redis and not redis.get("default", {}).get("url"):
        missing.append("redis: default.url configuration required")
    if needs_mq and not rabbitmq.get("default", {}).get("url"):
        missing.append("rabbitmq: default.url configuration required")
    data = {}
    try:
        keys = test_data_keys(case, scope_dir)
    except (ValueError, OSError, TypeError, KeyError, yaml.YAMLError) as exc:
        missing.append(str(exc))
        keys = set()
    for key in sorted(keys):
        if key not in profile.test_data:
            missing.append(f"test_data.{key}: configuration required")
        else:
            data[key] = resolved(profile.test_data[key], f"test_data.{key}")
    writes = write_reasons(case)
    why = "; ".join(writes)
    helpers = helper_calls(case)
    if profile.environment != "non-production":
        # Helpers never run elsewhere, even when declared read-only (ADR 0009).
        if writes:
            missing.append(f"writes require environment: non-production ({why})")
        elif helpers:
            missing.append(
                f"Python helpers require environment: non-production (helper {helpers[0]})"
            )
    if writes:
        if not case.data.isolated or not case.data.cleanup.strip():
            missing.append(f"writes require data.isolated and a data.cleanup plan ({why})")
        if not (
            case.teardown.steps
            or case.teardown.python
            or case.teardown.db
            or case.teardown.redis.delete
            or case.setup.redis
            or case.setup.rabbitmq
        ):
            missing.append(f"writes require executable teardown ({why})")
    return replace(
        profile,
        services=services,
        databases=databases,
        redis=redis,
        rabbitmq=rabbitmq,
        test_data=data,
        http=resolved(profile.http, "http"),
    ), missing
