"""Profile loader."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apitest.credential_guard import find_literal_credentials
from apitest.taint import TaintedStr

_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class ProfileError(ValueError):
    """Profile load/validate failure."""


class LiteralCredentials(ProfileError):
    """Profile files, which are committed, hold literal credentials."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("literal credentials in committed profiles:\n" + "\n".join(problems))
        self.problems = problems


@dataclass
class Profile:
    name: str
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    databases: dict[str, dict[str, Any]] = field(default_factory=dict)
    redis: dict[str, dict[str, Any]] = field(default_factory=dict)
    rabbitmq: dict[str, dict[str, Any]] = field(default_factory=dict)
    http: dict[str, Any] = field(default_factory=dict)
    test_data: dict[str, Any] = field(default_factory=dict)
    environment: str | None = None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ProfileError(f"profile not found: {path}")
    try:
        # Parsing a stream keeps the offending source line, maybe a secret, out of the error.
        with path.open() as stream:
            data = yaml.safe_load(stream) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"invalid profile YAML: {e}") from None
    if not isinstance(data, dict):
        raise ProfileError(f"profile must contain a mapping: {path}")
    return data


def _inherit(path: Path, seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    path = path.resolve()
    if path in seen:
        chain = " -> ".join(p.name for p in (*seen, path))
        raise ProfileError(f"profile inheritance cycle: {chain}")
    raw = _read_yaml(path)
    # Every file in the chain is committed, including parents whose values a child overrides.
    problems = find_literal_credentials(raw, path)
    if problems:
        raise LiteralCredentials(problems)
    extends = raw.get("extends")
    if extends is None:
        return raw
    if not isinstance(extends, str) or not extends.strip():
        raise ProfileError(f"extends must name a parent YAML file: {path}")
    return _merge(_inherit(path.parent / extends, (*seen, path)), raw)


def _merge(base: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for k, v in child.items():
        if k == "extends":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            if name not in os.environ:
                raise ProfileError(f"required env var not set: {name}")
            return str(TaintedStr(os.environ[name], source=name))

        substituted = _VAR_RE.sub(repl, value)
        if substituted != value:
            return TaintedStr(substituted, source="<composed>")
        return value
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _validate_services(data: dict[str, Any]) -> None:
    # The profile's `services:` map defines the valid service names. When
    # services are declared, every `databases:` entry must reference one of
    # them (internal-consistency guard; no hardcoded org registry).
    services = set(data.get("services") or {})
    if not services:
        return
    for name in data.get("databases") or {}:
        if name not in services:
            known = ", ".join(sorted(services)) or "<none>"
            raise ProfileError(
                f"database entry {name!r} has no matching service (declared services: {known})"
            )


def load_profile(
    name: str,
    profiles_dir: Path | str = "profiles",
    *,
    resolve: bool = True,
) -> Profile:
    profiles_dir = Path(profiles_dir)
    primary = profiles_dir / (name if name.endswith(".yaml") else f"{name}.yaml")
    raw = _read_yaml(primary)
    merged = _inherit(primary)

    # A parent profile cannot authorize writes to a child's different target.
    # Environment classification must be explicit in the selected profile.
    merged["environment"] = raw.get("environment")
    resolved = _substitute_env(merged) if resolve else merged
    _validate_services(resolved)

    return Profile(
        name=name,
        services=resolved.get("services") or {},
        databases=resolved.get("databases") or {},
        redis=resolved.get("redis") or {},
        rabbitmq=resolved.get("rabbitmq") or {},
        http=resolved.get("http") or {},
        test_data=resolved.get("test_data") or {},
        environment=resolved.get("environment"),
    )
