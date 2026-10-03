"""Reject literal credentials in profile files, which are committed to git."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

_ENV_REF = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}")
_SENSITIVE = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "apikey",
    "credential",
    "signature",
    "privatekey",
)
_URL_SECTIONS = {"databases": "DB", "redis": "REDIS", "rabbitmq": "MQ"}


def is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(word in normalized for word in _SENSITIVE)


def find_literal_credentials(data: Any, path: Path) -> list[str]:
    """One line per field that holds a literal credential. Values are never included."""
    problems: list[str] = []
    _walk(data, (), path, problems)
    return problems


def _walk(
    value: Any, keys: tuple[str, ...], path: Path, problems: list[str], name: str = ""
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _walk(item, (*keys, str(key)), path, problems, str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):  # items sit directly under the list's key
            _walk(item, (*keys, str(index)), path, problems, name)
    elif isinstance(value, str) and value:
        if is_sensitive_key(name) and not _ENV_REF.search(value):
            problems.append(_problem(path, keys, "holds a literal value", keys[:-1], keys[-1]))
        if "://" in value:
            problems.extend(_url_problems(value, keys, path))


def _url_problems(value: str, keys: tuple[str, ...], path: Path) -> list[str]:
    try:
        parts = urlsplit(value)
        password = parts.password
    except ValueError:  # not a parseable URL; nothing to check
        return []
    problems: list[str] = []
    if password and not _ENV_REF.fullmatch(password):
        problems.append(_problem(path, keys, "has a literal URL password", keys, "PASS"))
    for name, item in parse_qsl(parts.query, keep_blank_values=True):
        if is_sensitive_key(name) and item and not _ENV_REF.fullmatch(item):
            what = f"has a literal URL parameter {name!r}"
            problems.append(_problem(path, keys, what, keys, name))
    return problems


def _problem(
    path: Path, keys: tuple[str, ...], what: str, where: tuple[str, ...], leaf: str
) -> str:
    name = _suggest(path.stem, where, leaf)
    return (
        f"{path}: {'.'.join(keys)} {what}; put the value in .env as {name}=... "
        f"and write ${{{name}}} in its place"
    )


def _suggest(profile: str, where: tuple[str, ...], leaf: str) -> str:
    section, rest = (where[0], where[1:]) if where else ("", ())
    if section in _URL_SECTIONS:  # databases.<svc>.dsn, redis.<name>.url, rabbitmq.<name>.url
        parts = [rest[0] if section == "databases" and rest else "", _URL_SECTIONS[section], leaf]
    elif section == "services" and rest:  # services.<svc>[.roles.<role>].headers / base_url
        role = rest[2] if rest[1:2] == ("roles",) and len(rest) > 2 else ""
        parts = [rest[0], role, leaf]
    else:  # test_data.<key> and anything else
        parts = [*rest, leaf]
    name = re.sub(r"[^A-Za-z0-9]+", "_", "_".join(p for p in (profile, *parts) if p)).upper()
    return f"_{name}" if name[:1].isdigit() else name  # a valid ${VAR} name
