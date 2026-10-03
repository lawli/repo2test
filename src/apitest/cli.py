"""apitest CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import yaml

from apitest.credential_guard import find_literal_credentials
from apitest.envfile import load_env_file
from apitest.fixtures.db import DbClient
from apitest.manifest import read_manifest
from apitest.profile import LiteralCredentials, ProfileError, load_profile
from apitest.schema.case_v1 import Case
from apitest.secret_scan import scan_file
from apitest.selector import SelectorError, is_case_file, resolve_selector
from apitest.services import validate_service
from apitest.sql_lint import lint_sql
from apitest.taint import _TAINT_REGISTRY
from apitest.workspace import Workspace, find_workspace
from apitest.workspace_cli import CALLER_CWD, CallerPath


def _yaml_files(root: Path) -> list[Path]:
    """Every YAML under root except helper dirs (cases + fixtures; secret-scanned)."""
    return [
        p for p in root.rglob("*.yaml") if "_shared" not in p.parts and "_helpers" not in p.parts
    ]


def _case_files(root: Path) -> list[Path]:
    return [p for p in _yaml_files(root) if is_case_file(p)]


def _sql_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.sql"))


def _profile_credential_errors(profiles_dir: Path) -> list[str]:
    """Every profile file is committed, whether or not a run selects it."""
    if not profiles_dir.is_dir():
        return []
    errors: list[str] = []
    # Resolved like `_inherit` resolves them, so a symlinked profile is reported once.
    for f in sorted({path.resolve() for path in profiles_dir.glob("*.yaml")}):
        try:
            # Parsing a stream keeps the offending source line, maybe a secret, out of the error.
            with f.open() as stream:
                data = yaml.safe_load(stream)
        except yaml.YAMLError as e:
            errors.append(f"{f}: {e}")
            continue
        errors.extend(find_literal_credentials(data, f))
    return errors


# Heuristic rules warn; unambiguous hazards fail validation.
_SQL_LINT_WARNINGS = {"literal-id"}


def _schema_errors(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            Case.model_validate(yaml.safe_load(path.read_text()) or {})
        except Exception as e:  # noqa: BLE001 - report every kind of load failure
            errors.append(f"{path}: {e}")
    return errors


def _filter_by_tags(node_ids: list[str], tags: tuple[str, ...]) -> list[str]:
    """Keep node IDs whose case YAML carries ALL requested tags.

    A file that fails to parse is kept so pytest reports the real schema error
    instead of the tag filter masking it.
    """
    if not tags:
        return node_ids
    wanted = set(tags)
    out: list[str] = []
    for node_id in node_ids:
        path = Path(node_id.split("::", 1)[0])
        try:
            case = Case.model_validate(yaml.safe_load(path.read_text()) or {})
        except Exception:  # noqa: BLE001 - schema-invalid files surface at collection
            out.append(node_id)
            continue
        if wanted <= set(case.tags):
            out.append(node_id)
    return out


def _load_dotenv() -> None:
    """Load .env at the workspace root, or in CWD outside one. Shell env vars always win."""
    cwd = Path.cwd()
    load_env_file(find_workspace(cwd) or cwd)


@click.group()
@click.option("--workspace", type=click.Path(exists=True, file_okay=False), default=None)
@click.pass_context
def cli(ctx: click.Context, workspace: str | None = None) -> None:
    """Validate, run and maintain YAML API end-to-end test suites."""
    ctx.meta[CALLER_CWD] = os.getcwd()
    root = Path(workspace).resolve() if workspace else find_workspace()
    if root:
        os.chdir(root)
    _load_dotenv()


def _tests_root() -> Path:
    if find_workspace():
        return Workspace.load().path("tests")
    return Path(os.environ.get("APITEST_TESTS_ROOT", "tests"))


def _profiles_root() -> Path:
    return Workspace.load().path("profiles") if find_workspace() else Path("profiles")


@cli.command()
@click.option("--id", "ids", multiple=True, help="Canonical selector. Repeatable.")
@click.option("--service", default=None)
@click.option("--scenario", default=None)
@click.option("--suite", default=None)
@click.option("--case", default=None)
@click.option("--tag", "tags", multiple=True)
@click.option("--profile", default=None)
@click.option("--parallel", default="1")
@click.option("--report", type=click.Choice(["html", "junit", "both"]), default="html")
@click.option("--report-dir", type=CallerPath(file_okay=False), default=None)
@click.option("--fail-fast", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.option("-v", "--verbose", is_flag=True)
def run(
    ids: tuple[str, ...],
    service: str | None,
    scenario: str | None,
    suite: str | None,
    case: str | None,
    tags: tuple[str, ...],
    profile: str | None,
    parallel: str,
    report: str,
    report_dir: str | None,
    fail_fast: bool,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Run cases."""
    if ids and any([service, scenario, suite, case]):
        raise click.UsageError(
            "--id is mutually exclusive with --service/--scenario/--suite/--case"
        )

    tests_root = _tests_root()
    selectors: list[str] = list(ids)
    if not selectors:
        if not service:
            raise click.UsageError("either --id or --service required")
        if suite and not scenario:
            raise click.UsageError("--suite requires --scenario")
        if case and not scenario:
            raise click.UsageError("--case requires --scenario")
        parts = [service]
        if scenario:
            parts.append(scenario)
        if suite:
            parts.append(suite)
        if case:
            parts.append(case)
        selectors = ["/".join(parts)]

    node_ids: list[str] = []
    for sel in selectors:
        node_ids.extend(resolve_selector(sel, tests_root=tests_root))

    node_ids = _filter_by_tags(node_ids, tags)
    if not node_ids:
        click.echo(f"no cases match --tag {' '.join(tags)}", err=True)
        sys.exit(2)

    if dry_run:
        # Static preview: schema-check the selection here, since --collect-only
        # would otherwise list invalid cases as if they were runnable.
        errors = _schema_errors([Path(n.split("::", 1)[0]) for n in node_ids])
        if errors:
            for err in errors:
                click.echo(err, err=True)
            sys.exit(2)

    args = [sys.executable, "-m", "pytest", *node_ids]
    args += [
        "-o",
        f"apitest_tests_root={tests_root.resolve()}",
        "-o",
        f"apitest_profiles_dir={_profiles_root().resolve()}",
    ]
    env = None
    if find_workspace():
        # Only case helpers run code, under the write guard: pytest imports no conftest,
        # package __init__ or test module. Stray Python and unvalidated symlinked suites
        # are rejected rather than silently ignored.
        from apitest.validation import tree_problems

        problems = tree_problems(tests_root)
        for problem in problems:
            click.echo(problem, err=True)
        if problems:
            sys.exit(2)
        args += [
            "-c",
            str(Path.cwd() / "pytest.ini"),
            "--confcutdir",
            str(Path.cwd()),
            "--noconftest",
            "-p",
            "no:python",
        ]
        # Like -P, but inherited by xdist workers: the workspace root stays off sys.path,
        # so a root csv.py or html.py cannot shadow a module pytest imports.
        env = {**os.environ, "PYTHONSAFEPATH": "1"}
    if profile:
        args += ["-o", f"apitest_profile={profile}"]
    if parallel and parallel != "1":
        args += ["-n", parallel, "--dist", "loadgroup"]
    if fail_fast:
        args += ["-x"]
    if verbose:
        args += ["-v"]
    if dry_run:
        args += ["--collect-only"]
    else:
        rdir = Path(report_dir) if report_dir else Path("reports")
        rdir.mkdir(parents=True, exist_ok=True)
        args += [f"--apitest-summary-json={rdir / 'summary.json'}"]
        if report in ("junit", "both"):
            args += [f"--junitxml={rdir / 'junit.xml'}"]
        if report in ("html", "both"):
            args += [f"--html={rdir / 'index.html'}", "--self-contained-html"]

    sys.exit(subprocess.call(args, env=env))


@cli.command(name="list")
@click.option("--id", "ids", multiple=True, help="Path-prefix selector. Repeatable.")
@click.option("--service", default=None)
def list_cmd(ids: tuple[str, ...], service: str | None) -> None:
    """List cases that would run."""
    tests_root = _tests_root()
    selectors = list(ids)
    if service:
        selectors.append(service)
    if selectors:
        node_ids = []
        for sel in selectors:
            node_ids.extend(resolve_selector(sel, tests_root=tests_root))
    else:
        node_ids = [f"{p}::{p.stem}" for p in _case_files(tests_root)]
    for n in node_ids:
        click.echo(n)


@cli.command()
@click.argument("path", required=False, type=CallerPath())
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="if given, also check each case's service against this profile's services",
)
@click.option("--profiles-dir", type=CallerPath(file_okay=False), default=None)
@click.option("--base", default=None, help="Validate changed anchors against this git ref.")
def validate(
    path: str | None, profile_name: str | None, profiles_dir: str | None, base: str | None
) -> None:
    """Static-check cases."""
    p = Path(path) if path else _tests_root()
    if not p.exists():
        raise click.ClickException(f"Tests path does not exist: {p}")
    profiles = Path(profiles_dir) if profiles_dir else _profiles_root()
    errors: list[str] = _profile_credential_errors(profiles)
    known: set[str] | None = None
    if profile_name:
        try:
            known = set(load_profile(profile_name, profiles, resolve=False).services)
        except LiteralCredentials as e:
            # Normally already listed above; a parent outside the profiles dir is not.
            errors.extend(problem for problem in e.problems if problem not in errors)
        except ProfileError as e:
            errors.append(f"profile {profile_name!r} cannot be loaded: {e}")
    files = list(_yaml_files(p))
    for f in files:
        if is_case_file(f):
            try:
                data = yaml.safe_load(f.read_text()) or {}
                case = Case.model_validate(data)
                if known is not None:
                    from apitest.preflight import case_services

                    for service in case_services(case):
                        validate_service(service, known)
            except Exception as e:
                errors.append(f"{f}: {e}")
        for finding in scan_file(f):
            errors.append(f"{f}:{finding.line}: secret pattern {finding.kind}")
    sql_files = _sql_files(p)
    for f in sql_files:
        for issue in lint_sql(f.read_text(), path=f):
            line = f"{f}:{issue.line}: sql {issue.kind}: {issue.snippet}"
            if issue.kind in _SQL_LINT_WARNINGS:
                click.echo(f"warning: {line}", err=True)
            else:
                errors.append(line)
    from apitest.validation import validate_workspace

    if find_workspace():
        errors.extend(validate_workspace(Workspace.load(), base=base))
    if errors:
        for err in errors:
            click.echo(err, err=True)
        sys.exit(2)
    click.echo(f"validated {len(files)} yaml + {len(sql_files)} sql files: ok")


@cli.group()
def profile() -> None:
    """Profile commands."""


@profile.command("show")
@click.argument("name")
def profile_show(name: str) -> None:
    """Show a resolved profile with credentials redacted."""
    p = load_profile(name, _profiles_root())
    out: dict[str, Any] = {
        "name": p.name,
        "services": p.services,
        "databases": p.databases,
        "redis": p.redis,
        "rabbitmq": p.rabbitmq,
        "http": p.http,
    }
    text = json.dumps(out, indent=2, default=str)
    for tainted in sorted(_TAINT_REGISTRY, key=len, reverse=True):
        if tainted:
            text = text.replace(tainted, "***")
    click.echo(text)


@cli.group()
def connections() -> None:
    """Connection diagnostics."""


@connections.command("show")
@click.option("--profile", "profile_name", default="local")
@click.option("--parallel", default="1")
def connections_show(profile_name: str, parallel: str) -> None:
    """Connections a run can hold open: clients are created per case and closed
    at teardown (there is no pool), so the ceiling is workers x per-case."""
    p = load_profile(profile_name, _profiles_root())
    workers = max(1, int(parallel) if parallel != "auto" else os.cpu_count() or 1)
    per_case = {
        "http": 1,
        "mysql": int(bool(p.databases)),  # only when the case has setup/verify/teardown.db
        "redis": int(bool(p.redis)),
        "rabbitmq": int(bool(p.rabbitmq)),  # only when the case declares sniffers
    }
    click.echo(
        json.dumps(
            {
                "workers": workers,
                "per_case": per_case,
                "max_concurrent": workers * sum(per_case.values()),
            }
        )
    )


@cli.command()
@click.option("--manifest", "manifest_path", required=True, type=CallerPath(exists=True))
@click.option("--profile", "profile_name", default="local")
def sweep(manifest_path: str, profile_name: str) -> None:
    """Replay a cleanup manifest, deleting rows by exact ID."""
    rows = read_manifest(Path(manifest_path))
    p = load_profile(profile_name, _profiles_root())
    if p.environment != "non-production":
        raise click.ClickException("Cleanup writes require environment: non-production")
    db = DbClient(p)
    deleted = 0
    by_service: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_service.setdefault(r["service"], []).append(r)
    for service, entries in by_service.items():
        handle = db.for_service(service)
        for r in entries:
            with handle.transaction():
                handle.execute(
                    f"DELETE FROM {r['table']} WHERE id = %s",  # noqa: S608 - table from trusted manifest
                    r["id_value"],
                )
                deleted += 1
    db.close_all()
    click.echo(f"deleted {deleted} rows from {len(by_service)} services")


def main() -> None:
    try:
        cli()
    except (ProfileError, SelectorError) as exc:
        # One line with the fix instead of a traceback that buries it.
        raise SystemExit(f"Error: {exc}") from None


from apitest.workspace_cli import register  # noqa: E402

register(cli)

if __name__ == "__main__":
    main()
