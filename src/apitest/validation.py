"""Offline workspace validation. Helper analysis uses AST, never module execution."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from apitest.coverage import Endpoint, Evidence, load_inventory
from apitest.escape import HelperResolver
from apitest.fixtures.readonly_sql import check_verification_sql
from apitest.preflight import test_data_keys
from apitest.schema.case_v1 import Case
from apitest.selector import is_case_file, unguarded_python
from apitest.workspace import Workspace

_CATEGORIES = {
    "happy",
    "boundary",
    "validation",
    "auth",
    "state",
    "idempotency",
    "pagination",
    "dependency",
    "async",
    "error",
}
UNGUARDED_PYTHON = (
    "apitest never runs Python outside helper directories; move shared code into a "
    "_helpers module called from cases"
)


def tree_problems(tests: Path) -> list[str]:
    """Test-tree content that apitest would neither validate nor run as intended."""
    problems = [f"{path}: {UNGUARDED_PYTHON}" for path in unguarded_python(tests)]
    problems += [
        f"{path}: symlinked directories are not validated; use a real directory"
        for path in sorted(tests.rglob("*"))
        if path.is_symlink() and path.is_dir()
    ]
    return problems
_INJECTED = {"db", "http", "redis", "mq", "case", "profile", "ctx"}


def matcher_errors(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [error for v in value.values() for error in matcher_errors(v)]
    if isinstance(value, list):
        return [error for v in value for error in matcher_errors(v)]
    if not isinstance(value, str) or not value.startswith("@") or value.startswith("@@"):
        return []
    valid = value in {"@any", "@absent", "@null", "@uuid", "@iso8601"}
    valid |= bool(re.fullmatch(r"@number\s+(?:>=|<=|==|!=|>|<)\s*-?\d+(?:\.\d+)?", value))
    valid |= bool(re.fullmatch(r"@len\s+(?:(?:>=|<=|==|!=|>|<)\s*)?\d+", value))
    valid |= bool(
        re.fullmatch(
            r"@type (string|number|integer|object|array|boolean|null|"
            r"str|int|float|dict|list|bool|NoneType)",
            value,
        )
    )
    valid |= value.startswith("@contains ")
    if value.startswith("@in "):
        try:
            valid = isinstance(json.loads(value[4:]), list)
        except ValueError:
            valid = False
    if value.startswith("@regex /") and value.endswith("/"):
        try:
            re.compile(value[8:-1])
            valid = True
        except re.error:
            valid = False
    if valid:
        return []
    return [f"unknown or malformed matcher {value!r}; write '@{value}' for a literal value"]


def helper_errors(case: Case, case_path: Path, tests_root: Path) -> list[str]:
    errors = []
    resolver = HelperResolver(tests_root)
    for call in case.setup.python + case.verify.python + case.teardown.python:
        parts = call.call.split(".")
        if len(parts) != 3 or parts[0] != "helpers":
            errors.append(f"invalid helper reference {call.call}")
            continue
        candidates = [p / f"{parts[1]}.py" for p in resolver._scope_chain(case_path)]
        candidates.append(Path(__file__).parent / "helpers" / f"{parts[1]}.py")
        definitions: list[ast.FunctionDef] = []
        for path in dict.fromkeys(candidates):
            if path.is_file():
                tree = ast.parse(path.read_text(), filename=str(path))
                definitions.extend(
                    n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == parts[2]
                )
        overrides = [
            n
            for n in definitions
            if any(
                isinstance(d, ast.Call)
                and getattr(d.func, "id", None) == "override"
                and len(d.args) == 1
                and isinstance(d.args[0], ast.Constant)
                and d.args[0].value == call.call
                for d in n.decorator_list
            )
        ]
        selected = overrides if overrides else definitions
        if len(selected) != 1:
            errors.append(f"helper {call.call}: missing or ambiguous function")
            continue
        fn = selected[0]
        params = fn.args.args + fn.args.kwonlyargs
        names = {a.arg for a in params}
        needed = {
            "db": bool(
                case.requires.databases or case.setup.db or case.verify.db or case.teardown.db
            ),
            "redis": bool(
                case.requires.redis
                or case.setup.redis
                or case.verify.redis
                or case.teardown.redis.delete
            ),
            "mq": bool(case.requires.rabbitmq or case.setup.rabbitmq or case.verify.rabbitmq),
        }
        for dependency in names & needed.keys():
            if not needed[dependency]:
                errors.append(f"helper {call.call}: declare {dependency} in requires")
        required = {a.arg for a in fn.args.args[: len(fn.args.args) - len(fn.args.defaults)]}
        required |= {
            a.arg
            for a, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True)
            if default is None
        }
        missing = required - _INJECTED - call.args.keys()
        unknown = set() if fn.args.kwarg else call.args.keys() - names
        clashes = call.args.keys() & _INJECTED
        if missing or unknown or clashes or fn.args.posonlyargs:
            errors.append(
                f"helper {call.call}: missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}, injected={sorted(clashes)}"
            )
    return errors


def migrate_case(content: str) -> str:
    """Validate current v1 without rewriting author-owned YAML.

    There is no schema-version transition yet. Future migrations must make only
    necessary versioned edits, preserving comments, formatting and anchors.
    """
    Case.model_validate(yaml.safe_load(content))
    return content


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode:
        raise ValueError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc.stdout


def _base_content(root: Path, base: str, rel: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"{base}:{rel}"],
        capture_output=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def defect_reference_error(root: Path, ref: str) -> str | None:
    """Accept explicit tracker IDs, HTTP issue links, or existing local records."""
    if re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+|(?:[\w.-]+/[\w.-]+)?#\d+", ref):
        return None
    url = urlsplit(ref)
    if url.scheme in {"http", "https"} and url.netloc and not any(c.isspace() for c in ref):
        return None
    reference = (root / ref).resolve()
    if ref.startswith("defects/") and reference.is_relative_to(root / "defects"):
        if reference.is_file() and reference.suffix == ".md":
            return None
        return f"missing defect record {ref}"
    return (
        f"invalid defect reference {ref!r}: use PROJ-123, #123, owner/repo#123, "
        "an HTTP URL, or defects/*.md"
    )


def _unsupported_evidence(
    fragment: Path,
    checkouts: Path,
    commit: str | None,
    evidence: list[Evidence],
    cache: dict[Path, list[str] | None],
) -> list[str]:
    """Citations that commit's checkout cannot support. Without the checkout, as in CI,
    nothing is checked."""
    if not commit or not (checkout := checkouts / commit).is_dir():
        return []
    errors = []
    for e in evidence:
        cited = f"{fragment}: evidence {e.file}:{e.line}"
        if Path(e.file).is_absolute() or ".." in Path(e.file).parts:
            errors.append(f"{cited} is outside the source checkout")
            continue
        file = checkout / e.file
        if file not in cache:
            cache[file] = file.read_text(errors="replace").splitlines() if file.is_file() else None
        lines = cache[file]
        if lines is None:
            errors.append(f"{cited} names a file the source checkout does not have")
        elif e.line > len(lines):
            errors.append(f"{cited} is past the end of the file")
        elif not lines[e.line - 1].strip():
            errors.append(f"{cited} is a blank line")
    return errors


def validate_workspace(workspace: Workspace, *, base: str | None = None) -> list[str]:
    errors: list[str] = []
    root, tests = workspace.root, workspace.path("tests")
    errors.extend(workspace.runner_problems())
    rows: dict[tuple[str, str], Any] = {}
    try:
        inventory = load_inventory(workspace.path("coverage"))
    except Exception as exc:
        return [f"coverage: {exc}"]
    changed: set[str] | None = None
    upgrade = False
    if base:
        if base.startswith("-"):
            return ["invalid base ref"]
        try:
            base = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
            changed = set(_git(root, "diff", "--name-only", base, "--").splitlines())
            changed.update(_git(root, "ls-files", "--others", "--exclude-standard").splitlines())
            upgrade = any(
                p == "uv.lock" or (p.startswith("vendor/") and p.endswith(".whl")) for p in changed
            )
        except ValueError as exc:
            return [str(exc)]
    if not workspace.source_commit:
        errors.append("reference.commit is required")
    checkouts = workspace.path("source", ".source")
    cited_lines: dict[Path, list[str] | None] = {}
    for path, endpoint in inventory:
        if not endpoint.rows:
            errors.append(f"{path}: endpoint must retain a coverage or gap row")
        errors.extend(
            _unsupported_evidence(
                path, checkouts, workspace.source_commit, endpoint.evidence, cited_lines
            )
        )
        prior_rows = {}
        if base:
            prior = _base_content(root, base, path.relative_to(root).as_posix())
            if prior:
                old = Endpoint.model_validate(yaml.safe_load(prior))
                prior_rows = {(r.key, r.variant): r.model_dump() for r in old.rows}
        for row in endpoint.rows:
            key = (row.key, row.variant)
            if key in rows:
                errors.append(f"{path}: duplicate coverage row {key}")
            rows[key] = row
            errors.extend(
                _unsupported_evidence(
                    path, checkouts, row.source_commit, row.evidence, cited_lines
                )
            )
            parts = row.key.split("|", 4)
            if (
                len(parts) != 5
                or parts[:3] != [endpoint.service, endpoint.method.upper(), endpoint.path]
                or parts[3] not in _CATEGORIES
                or not parts[4]
                or not endpoint.path.startswith("/")
                or endpoint.method != endpoint.method.upper()
            ):
                errors.append(f"{path}: invalid coverage key {row.key!r}")
            if row.expectation == "confirmed" and not any(
                e.kind in {"requirement", "declarative", "test"} for e in row.evidence
            ):
                errors.append(f"{path}: confirmed expectations require contract evidence")
            if (
                base
                and row.model_dump() != prior_rows.get(key)
                and row.source_commit != workspace.source_commit
            ):
                errors.append(f"{path}: changed coverage row has stale source_commit: {row.key}")
    errors.extend(tree_problems(tests))
    claimed: set[tuple[str, str]] = set()
    declared_data = set(workspace.config.get("test_data", {}).get("keys", []))
    for path in sorted(tests.rglob("*.yaml")):
        if not is_case_file(path):
            continue
        try:
            content_bytes = path.read_bytes()
            content = content_bytes.decode("utf-8")
            case = Case.model_validate(yaml.safe_load(content))
            for verify in case.verify.db:
                check_verification_sql(verify.sql, template=True)
            if not case.source_commit or not case.covers:
                errors.append(f"{path}: source_commit and covers are required in a workspace")
            if not case.steps or not any(
                s.assert_.status is not None
                or s.assert_.json_
                or s.assert_.headers
                or s.assert_.text is not None
                for s in case.steps
            ):
                errors.append(f"{path}: an executable business assertion is required")
            for cover in case.covers:
                key = (cover.key, cover.variant)
                if key in claimed:
                    errors.append(f"{path}: duplicate case coverage {key}")
                claimed.add(key)
                if key not in rows:
                    errors.append(f"{path}: covers references a missing row {key}")
                elif case.expectation == "confirmed" and rows[key].expectation == "pending":
                    errors.append(f"{path}: confirmed case claims a pending expectation {key}")
                elif case.source_commit != rows[key].source_commit:
                    errors.append(f"{path}: case and coverage source_commit differ {key}")
            for marker in case.known_defects:
                if error := defect_reference_error(root, marker.ref):
                    errors.append(f"{path}: {error}")
            for data_key in test_data_keys(case, path.parent) - declared_data:
                errors.append(f"{path}: undeclared test_data key {data_key}")
            errors.extend(f"{path}: {e}" for e in helper_errors(case, path, tests))
            steps = case.setup.steps + case.steps + case.teardown.steps
            files = [*case.setup.db, *case.setup.redis, *case.teardown.db]
            for step in steps:
                errors.extend(
                    f"{path}: {e}" for e in matcher_errors(step.assert_.model_dump(by_alias=True))
                )
                files.extend(step.request.files.values())
                if step.request.body_template:
                    files.append(step.request.body_template)
            for rel in files:
                file = (path.parent / rel).resolve()
                if not file.is_relative_to(root) or not file.is_file():
                    errors.append(f"{path}: fixture missing or outside workspace: {rel}")
            rel = path.relative_to(root).as_posix()
            if base and changed is not None and rel in changed:
                old_content = _base_content(root, base, rel)
                migrated = bool(
                    upgrade
                    and old_content
                    and migrate_case(old_content.decode("utf-8")).encode("utf-8") == content_bytes
                    and yaml.safe_load(old_content).get("source_commit") == case.source_commit
                    and yaml.safe_load(old_content).get("covers")
                    == yaml.safe_load(content).get("covers")
                )
                if not migrated and case.source_commit != workspace.source_commit:
                    errors.append(f"{path}: changed case has stale source_commit")
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    for key, row in rows.items():
        if row.status == "authored" and key not in claimed:
            errors.append(f"authored coverage row has no case: {key}")
    return errors
