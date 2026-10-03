"""Jinja2 templating wrapper."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateSyntaxError,
    Undefined,
    nodes,
)

_DOLLAR_RE = re.compile(r"\$\{([^}]+)\}")
_SINGLE_EXPR_RE = re.compile(r"^\$\{([^}]+)\}$")


def _globals() -> dict[str, Any]:
    return {
        "uuid4": lambda: str(uuid.uuid4()),
        "now": lambda: datetime.now(UTC).isoformat(),
        "today_iso": lambda: datetime.now(UTC).date().isoformat(),
        "random_phone": lambda: f"+1555{uuid.uuid4().int % 10_000_000:07d}",
        "random_email": lambda: f"u{uuid.uuid4().hex[:8]}@example.com",
    }


def _to_jinja(s: str) -> str:
    return _DOLLAR_RE.sub(r"{{ \1 }}", s)


def template_dependencies(text: str) -> tuple[set[str], bool, list[str]]:
    """Inspect Jinja without evaluating it. Dynamic data access needs a declaration."""
    tree = _env().parse(_to_jinja(text))
    keys: set[str] = set()
    dynamic = False
    includes: list[str] = []

    def access(node: nodes.Node | None) -> list[str | None] | None:
        if isinstance(node, nodes.Name):
            return [node.name]
        if isinstance(node, (nodes.Getattr, nodes.Getitem)):
            base = access(node.node)
            key = (
                node.attr
                if isinstance(node, nodes.Getattr)
                else (node.arg.value if isinstance(node.arg, nodes.Const) else None)
            )
            return [*base, key if isinstance(key, str) else None] if base else None
        if isinstance(node, nodes.Filter) and node.name == "attr":
            base = access(node.node)
            arg = node.args[0] if node.args else None
            key = arg.value if isinstance(arg, nodes.Const) else None
            return [*base, key if isinstance(key, str) else None] if base else None
        return None

    def visit(node: nodes.Node, parent: nodes.Node | None = None) -> None:
        nonlocal dynamic
        chain = access(node)
        extended = (
            parent is not None
            and access(parent) is not None
            and (getattr(parent, "node", None) is node)
        )
        if chain and not extended:
            prefix = ["ctx", "profile", "test_data"]
            if all(part in (expected, None) for part, expected in zip(chain, prefix, strict=False)):
                if len(chain) >= 4 and chain[:3] == prefix and chain[3] is not None:
                    keys.add(chain[3])
                else:
                    dynamic = True
        if isinstance(node, (nodes.Include, nodes.Extends, nodes.Import, nodes.FromImport)):
            template = node.template
            if isinstance(template, nodes.Const) and isinstance(template.value, str):
                includes.append(template.value)
            else:
                dynamic = True
        for child in node.iter_child_nodes():
            visit(child, node)

    visit(tree)
    return keys, dynamic, includes


def ctx_references(text: str) -> set[str]:
    """Names a template reads as `ctx.<name>`, excluding case/profile metadata."""
    try:
        tree = _env().parse(_to_jinja(text))
    except TemplateSyntaxError:
        return set()
    refs: set[str] = set()
    for node in tree.find_all((nodes.Getattr, nodes.Getitem)):
        key: object = None
        if isinstance(node, nodes.Getattr):
            base, key = node.node, node.attr
        elif isinstance(node, nodes.Getitem):
            base = node.node
            key = node.arg.value if isinstance(node.arg, nodes.Const) else None
        else:
            continue
        is_ctx = isinstance(base, nodes.Name) and base.name == "ctx"
        if is_ctx and isinstance(key, str) and key not in {"case", "profile"}:
            refs.add(key)
    return refs


class _Box:
    """Attribute-access view over a dict (so `ctx.items` is the key, not dict.items)."""

    def __init__(self, d: dict[str, Any]) -> None:
        self._raw = d
        for k, v in d.items():
            setattr(self, k, _Box(v) if isinstance(v, dict) else v)


def _resolve(ctx_vars: dict[str, Any]) -> dict[str, Any]:
    """Convert nested dicts so attribute access works in Jinja templates."""
    return {k: (_Box(v) if isinstance(v, dict) else v) for k, v in ctx_vars.items()}


def _env() -> Environment:
    env = Environment(undefined=StrictUndefined, autoescape=False)
    env.globals.update(_globals())
    return env


def render_string(s: str, vars_: dict[str, Any]) -> str:
    return _env().from_string(_to_jinja(s)).render(**_resolve(vars_))


def render_value(s: str, vars_: dict[str, Any]) -> Any:
    """Render `s`; a string that is exactly one `${expr}` yields the expression's
    native value (int stays int, dict stays dict) instead of its str() form."""
    m = _SINGLE_EXPR_RE.fullmatch(s)
    if m is None:
        return render_string(s, vars_)
    value = _env().compile_expression(m.group(1), undefined_to_none=False)(**_resolve(vars_))
    if isinstance(value, Undefined):
        str(value)  # StrictUndefined raises here with the standard message
    return value._raw if isinstance(value, _Box) else value


def render_template(path: Path | str, vars_: dict[str, Any]) -> str:
    p = Path(path)
    env = Environment(
        loader=FileSystemLoader(str(p.parent)),
        undefined=StrictUndefined,
        autoescape=False,
    )
    env.globals.update(_globals())
    text = _to_jinja(p.read_text())
    return env.from_string(text).render(**_resolve(vars_))
