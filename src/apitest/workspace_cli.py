"""Workspace lifecycle, offline authoring support and reproducible distribution."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import click
import yaml
from pydantic import ValidationError

from apitest.bundle import Bundle
from apitest.coverage import coverage_view, load_inventory, reconcile
from apitest.preflight import prepare_profile
from apitest.profile import load_profile
from apitest.schema.case_v1 import Case
from apitest.selector import is_case_file
from apitest.validation import migrate_case, tree_problems
from apitest.workspace import Workspace

CALLER_CWD = "apitest.caller_cwd"


class CallerPath(click.Path):
    """A path typed by the user, relative to where they ran the command.

    `apitest` enters the workspace root before subcommand arguments are parsed.
    Explicit relative paths still mean the caller's directory; defaults keep
    their workspace-relative meaning.
    """

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> Any:
        base = ctx.meta.get(CALLER_CWD) if ctx is not None else None
        if (
            base
            and param is not None
            and param.name is not None
            and ctx is not None
            and ctx.get_parameter_source(param.name) is not click.core.ParameterSource.DEFAULT
            and not Path(os.fspath(value)).is_absolute()
        ):
            value = os.path.join(base, os.fspath(value))
        return super().convert(value, param, ctx)


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise click.ClickException(f"{command[0]} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _cases(root: Path, only: set[str] | None = None) -> list[tuple[Path, Case]]:
    cases = []
    for p in sorted(root.rglob("*.yaml")):
        if not is_case_file(p) or (only is not None and str(p) not in only):
            continue
        try:
            cases.append((p, Case.model_validate(yaml.safe_load(p.read_text()))))
        except (yaml.YAMLError, ValidationError) as e:
            raise click.ClickException(f"{p}: {e}; run `apitest validate` for details") from None
    return cases


def _dump(value: Any) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


def _read_bundle(path: Path) -> Bundle:
    try:
        return Bundle.read(path)
    except (ValueError, KeyError, OSError) as exc:
        raise click.ClickException(f"Unusable runner wheel {path.name}: {exc}") from exc


def _recorded_ref(reference: str, commit: str) -> str:
    """Refs such as HEAD, @{-1} or FETCH_HEAD name local state; record their commit instead."""
    local = re.match(r"(?:HEAD|[A-Z_]+_HEAD|@)(?:$|[~^@])", reference)
    return commit if local or "@{" in reference else reference


def _version_key(version: str | None) -> tuple[int, ...]:
    """Release order: 0.2.3rc1 < 0.2.3 == 0.2.3+local."""
    match = re.match(r"(\d+(?:\.\d+)*)(.*)", version or "")
    if match is None:
        return ()
    numbers = [int(part) for part in match.group(1).split(".")]
    prerelease = re.match(r"[.-]?(?:a|b|c|rc|alpha|beta|pre|dev)", match.group(2), re.I)
    return (*numbers, -1 if prerelease else 0)


def release_notes(notes: str, current: str | None, new: str) -> str:
    """Keep the preamble and the sections for releases after `current` up to `new`."""
    preamble, *sections = re.split(r"(?m)^(?=## )", notes)
    # A pre-release of X already carries X's changes, so it gets X's notes.
    upper = (*_version_key(new)[:-1], 0)
    selected = [
        section
        for section in sections
        if _version_key(current)
        < _version_key(section.removeprefix("## ").split("\n", 1)[0].strip())
        <= upper
    ]
    if not selected:
        return f"No compatibility notes between {current or 'unknown'} and {new}.\n"
    return preamble + "".join(selected)


def source_checkout(workspace: Workspace, ref: str) -> tuple[Path, str]:
    target_path = workspace.config.get("target", {}).get("path")
    if not target_path or ref.startswith("-"):
        raise click.ClickException("target.path and a non-option reference are required")
    target = Path(target_path)
    target = (workspace.root / target).resolve()
    commit = _run(["git", "-C", str(target), "rev-parse", "--verify", f"{ref}^{{commit}}"])
    destination = workspace.path("source", ".source") / commit
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run(
            ["git", "clone", "--no-hardlinks", "--no-checkout", "--", str(target), str(destination)]
        )
        _run(["git", "-C", str(destination), "checkout", "--detach", commit])
        for path in destination.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and ".git" not in path.relative_to(destination).parts
            ):
                path.chmod(path.stat().st_mode & ~0o222)
    actual = _run(["git", "-C", str(destination), "rev-parse", "HEAD"])
    dirty = _run(["git", "-C", str(destination), "status", "--porcelain"])
    if actual != commit or dirty:
        raise click.ClickException("Existing source checkout HEAD does not match requested ref")
    return destination, commit


@click.group()
def workspace() -> None:
    """Initialize and diagnose a self-contained test repository."""


@workspace.command("init")
@click.argument("directory", type=CallerPath(path_type=Path))
@click.option(
    "--target", type=CallerPath(exists=True, file_okay=False, path_type=Path), required=True
)
@click.option(
    "--ref", "reference", required=True, help="Explicit deployed or estimated source ref."
)
@click.option(
    "--estimated-because", default=None, help="Record why the selected ref is an estimate."
)
@click.option(
    "--runner-wheel", type=CallerPath(exists=True, dir_okay=False, path_type=Path), required=True
)
@click.option(
    "--runner-owner", required=True, help="Platform username/group for runner CODEOWNERS."
)
@click.option("--ci", type=click.Choice(["github", "gitlab"]), required=True)
@click.option(
    "--offline", is_flag=True, help="Resolve third-party dependencies from uv cache only."
)
def initialize(
    directory: Path,
    target: Path,
    reference: str,
    estimated_because: str | None,
    runner_wheel: Path,
    runner_owner: str,
    ci: str,
    offline: bool,
) -> None:
    """Create a local workspace; never create or push a remote repository."""
    root = directory.resolve()
    target = Path(_run(["git", "-C", str(target.resolve()), "rev-parse", "--show-toplevel"]))
    if root.exists() and any(root.iterdir()):
        raise click.ClickException(
            "Choose an empty destination; existing workspace files are preserved"
        )
    if root == target or root.is_relative_to(target):
        raise click.ClickException(
            "The test workspace must be separate from the business repository"
        )
    if not re.fullmatch(r"@[\w./-]+", runner_owner):
        raise click.ClickException("--runner-owner must name one platform user/group (e.g. @qa)")
    if reference.startswith("-"):
        raise click.ClickException("Invalid reference")
    commit = _run(["git", "-C", str(target), "rev-parse", "--verify", f"{reference}^{{commit}}"])
    bundle = _read_bundle(runner_wheel)
    version = bundle.version
    existed = root.exists()
    root.mkdir(parents=True, exist_ok=True)
    for name in ("tests", "coverage", "profiles", "vendor", "tools", "defects"):
        (root / name).mkdir()
    for name in ("tests", "coverage", "defects"):
        (root / name / ".gitkeep").write_bytes(b"")
    wheel = root / "vendor" / runner_wheel.name
    shutil.copy2(runner_wheel, wheel)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    q = json.dumps
    (root / "repo2test.toml").write_text(
        f"format = 1\n[target]\npath = {q(os.path.relpath(target, root))}\n"
        f"[reference]\nref = {q(_recorded_ref(reference, commit))}\ncommit = {q(commit)}\n"
        f"estimated = {str(estimated_because is not None).lower()}\n"
        f"evidence = {q(estimated_because or 'user-selected deployed reference')}\n"
        '[paths]\ntests = "tests"\nprofiles = "profiles"\ncoverage = "coverage"\n'
        'source = ".source"\nskill = ".claude/skills/repo2test-workspace/SKILL.md"\n'
        "[test_data]\nkeys = []\n"
    )
    (root / "runner.toml").write_text(
        _set_table(
            "",
            "runner",
            {
                "wheel": "vendor/" + wheel.name,
                "sha256": digest,
                "version": version,
                **bundle.provenance,
            },
        )
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "repo2test-workspace"\nversion = "0.0.0"\n'
        f'requires-python = ">=3.11"\ndependencies = ["apitest=={version}"]\n'
        f"[tool.uv.sources]\napitest = {{path = {q('vendor/' + wheel.name)}}}\n"
    )
    (root / "pytest.ini").write_text(
        "[pytest]\napitest_tests_root = tests\napitest_profiles_dir = profiles\n"
        "apitest_profile = local\n"
    )
    (root / ".gitignore").write_text(
        ".venv/\n.env\n.env.*\n!.env.example\n/.source/\n/reports/\n__pycache__/\n.pytest_cache/\n"
    )
    (root / "profiles/local.yaml").write_text("services: {}\n")
    bundle.install_assets(root)
    bundle.protect_checkout(root)
    (root / "AGENTS.md").write_text(
        "For repo2test test authoring, environment setup, suite execution or maintenance, "
        "read `.claude/skills/repo2test-workspace/SKILL.md` and use the workspace's locked "
        "runner. Before authoring or running cases, run `uv sync --locked` and "
        "`uv run --locked apitest workspace doctor` here and continue only when doctor passes. "
        "Target business source is read-only.\n"
    )
    _write_ci(root, ci, runner_owner)
    (root / "README.md").write_text(
        "# API tests\n\nRun `uv sync --locked`, then `uv run --locked apitest --help`.\n"
        "Configure service URLs with `apitest workspace configure`; "
        "environment values stay local.\n"
        "Install the entry with `python3 tools/install_entry.py --host codex` or `--host claude`.\n"
        "Use `--update` to deliberately replace an installed entry. For sibling workspace access, "
        'start Codex with `--add-dir <workspace> --add-dir "$(uv cache dir)"` (add '
        "`-c sandbox_workspace_write.network_access=true` when uv must download packages); "
        "start Claude Code with `--add-dir <workspace>`.\n\n"
        "Platform setup: require the validate check and up-to-date branches "
        "or a merge queue/train; require CODEOWNERS approval by a runner maintainer "
        "other than the PR author for wheel/lock "
        "changes. Enable the default-branch push check. These platform settings must be configured "
        "by a repository administrator; generating YAML does not enforce them.\n"
    )
    try:
        _run(["uv", "lock", *(["--offline"] if offline else [])], cwd=root)
    except (click.ClickException, OSError) as exc:
        # The destination was empty or absent; restore that so the same command can be rerun.
        if existed:
            for child in root.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            shutil.rmtree(root, ignore_errors=True)
        detail = exc.message if isinstance(exc, click.ClickException) else str(exc)
        raise click.ClickException(
            f"{detail}. The partial workspace was removed; fix package access (or use "
            "--offline with a populated uv cache) and rerun the same command."
        ) from None
    click.echo(f"Initialized {root}; run uv sync --locked there. No remote was created.")


def _protect_runner(root: Path, owner: str, owners_path: Path) -> None:
    protected = (
        "/vendor/*.whl",
        "/uv.lock",
        "/pyproject.toml",
        "/.gitattributes",
        "/runner.toml",
        "/.claude/skills/repo2test-workspace/",
        "/tools/",
        "/.github/workflows/",
        "/.gitlab-ci.yml",
        "/" + owners_path.relative_to(root).as_posix(),
    )
    existing = owners_path.read_text() if owners_path.exists() else ""
    # Replace only legacy rules generated for this owner. Preserve team rules.
    legacy = {f"/repo2test.toml {owner}", f"/helper-trust.toml {owner}"}
    existing = "\n".join(line for line in existing.splitlines() if line not in legacy)
    additions = [
        f"{path} {owner}" for path in protected if f"{path} {owner}" not in existing.splitlines()
    ]
    owners_path.write_text(existing.rstrip() + "\n" + "\n".join(additions) + "\n")


def _write_ci(root: Path, provider: str, owner: str) -> None:
    if provider == "github":
        folder = root / ".github/workflows"
        folder.mkdir(parents=True)
        _protect_runner(root, owner, root / ".github/CODEOWNERS")
        (folder / "validate.yml").write_text(
            "name: validate\non: [pull_request, push, merge_group]\n"
            "permissions:\n  contents: read\n"
            "jobs:\n  validate:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n"
            "      - uses: astral-sh/setup-uv@v5\n"
            "      - run: uv sync --locked\n"
            "      - name: Validate merged candidate\n        env:\n"
            "          BASE: ${{ github.event.pull_request.base.sha || "
            "github.event.merge_group.base_sha || github.event.before }}\n"
            '        run: |\n          if [ -n "$BASE" ] && '
            'git cat-file -e "$BASE^{commit}" 2>/dev/null; then\n'
            '            uv run --locked apitest validate --base "$BASE"\n'
            "          else\n            uv run --locked apitest validate\n          fi\n"
        )
    else:
        _protect_runner(root, owner, root / "CODEOWNERS")
        (root / ".gitlab-ci.yml").write_text(
            'image: python:3.12\nvariables:\n  GIT_DEPTH: "0"\n'
            "workflow:\n  rules:\n    - if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'\n"
            "    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'\n"
            "validate:\n  script:\n"
            "    - pip install uv\n    - uv sync --locked\n"
            '    - |\n      if [ "$CI_MERGE_REQUEST_EVENT_TYPE" = "detached" ]; then\n'
            '        echo "Enable merged results pipelines and merge trains"; exit 2\n      fi\n'
            '      BASE="${CI_MERGE_REQUEST_TARGET_BRANCH_SHA:-$CI_COMMIT_BEFORE_SHA}"\n'
            '      if [ -n "$BASE" ] && git cat-file -e "$BASE^{commit}" 2>/dev/null; then\n'
            '        uv run --locked apitest validate --base "$BASE"\n'
            "      else\n        uv run --locked apitest validate\n      fi\n"
        )


@workspace.command("source")
@click.option("--ref", "reference", default=None)
def source(reference: str | None) -> None:
    """Create an isolated read-only source checkout."""
    ws = Workspace.load()
    ref = reference or ws.source_commit or str(ws.config.get("reference", {}).get("ref", "HEAD"))
    path, commit = source_checkout(ws, ref)
    _dump(
        {
            "checkout": str(path),
            "source_commit": commit,
            "reference_update_required": commit != ws.source_commit,
        }
    )


def _set_table(text: str, table: str, values: dict[str, Any]) -> str:
    """Change selected scalar values while preserving other tables and comments."""
    match = re.search(rf"(?m)^\[{re.escape(table)}\]\s*$", text)
    if match is None:
        text += f"\n[{table}]\n"
        return _set_table(text, table, values)
    start = match.end()
    following = re.search(r"(?m)^\[", text[start:])
    end = start + following.start() if following else len(text)
    body = text[start:end].rstrip() + "\n"
    for key, value in values.items():
        line = f"{key} = {json.dumps(value)}"
        pattern = rf"(?m)^{re.escape(key)}\s*=.*$"
        body = (
            re.sub(pattern, line.replace("\\", "\\\\"), body)
            if re.search(pattern, body)
            else body + line + "\n"
        )
    return text[:start] + body + text[end:]


@workspace.command("reference")
@click.option("--ref", "reference", required=True)
@click.option("--estimated-because", default=None, help="Record fallback deployment-ref evidence.")
def update_reference(reference: str, estimated_because: str | None) -> None:
    """Select the deployed reference; never bulk-rewrite case or coverage anchors."""
    ws = Workspace.load()
    path, commit = source_checkout(ws, reference)
    review, comparison = _rows_to_review(ws, path, commit)
    marker = ws.root / "repo2test.toml"
    marker.write_text(
        _set_table(
            marker.read_text(),
            "reference",
            {
                "ref": _recorded_ref(reference, commit),
                "commit": commit,
                "estimated": estimated_because is not None,
                "evidence": estimated_because or "user-selected deployed reference",
            },
        )
    )
    _dump(
        {
            "checkout": str(path),
            "source_commit": commit,
            "previous_source_commit": ws.source_commit,
            "comparison": comparison,
            "review": review,
            "review_required": "Review each listed row and its cases before re-anchoring them",
        }
    )


def _rows_to_review(ws: Workspace, checkout: Path, commit: str) -> tuple[list[dict[str, Any]], str]:
    """Coverage rows whose evidence files changed between their own anchor and `commit`.

    Each row is compared from its own `source_commit`, so a row nobody re-anchored stays
    listed across later reference updates."""
    diffs: dict[str, set[str] | None] = {}

    def changed_since(anchor: str) -> set[str] | None:
        if anchor not in diffs:
            diffs[anchor] = None
            # Only a full object id names the same commit in the fresh reference clone.
            if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", anchor):
                with contextlib.suppress(click.ClickException):
                    diff = _run(
                        [
                            "git",
                            "-C",
                            str(checkout),
                            "diff",
                            "--no-renames",
                            "--name-only",
                            anchor,
                            commit,
                        ]
                    )
                    diffs[anchor] = set(diff.splitlines())
        return diffs[anchor]

    claims: dict[tuple[str, str], list[str]] = {}
    for case_path in sorted(ws.path("tests").rglob("*.yaml")):
        if not is_case_file(case_path):
            continue
        try:
            case = Case.model_validate(yaml.safe_load(case_path.read_text()))
        except Exception:  # noqa: BLE001 - validation reports invalid cases separately
            continue
        for cover in case.covers:
            claims.setdefault((cover.key, cover.variant), []).append(
                case_path.relative_to(ws.root).as_posix()
            )

    def touched(files: set[str], changed: set[str] | None) -> list[str]:
        if changed is None:
            return sorted(files)
        return sorted(f for f in files if f in changed or any(c.endswith("/" + f) for c in changed))

    review = []
    try:
        inventory = load_inventory(ws.path("coverage"))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    for fragment, endpoint in inventory:
        endpoint_files = {e.file for e in endpoint.evidence}
        for row in endpoint.rows:
            if row.source_commit == commit:
                continue
            changed = changed_since(row.source_commit)
            evidence = touched(endpoint_files | {e.file for e in row.evidence}, changed)
            if changed is None or evidence:
                review.append(
                    {
                        "fragment": fragment.relative_to(ws.root).as_posix(),
                        "key": row.key,
                        "variant": row.variant,
                        "anchor": row.source_commit,
                        "changed_evidence": evidence,
                        "cases": claims.get((row.key, row.variant), []),
                    }
                )
    comparison = f"git diff <row source_commit>..{commit[:12]}" if diffs else "all rows anchored"
    if unavailable := sorted(anchor for anchor, diff in diffs.items() if diff is None):
        comparison += f"; anchors unavailable, review every row at: {', '.join(unavailable)}"
    return review, comparison


@workspace.command("upgrade")
@click.option("--runner-wheel", type=CallerPath(exists=True, path_type=Path), required=True)
@click.option("--offline", is_flag=True)
@click.option("--migrate", "migrate_cases", is_flag=True)
def upgrade(runner_wheel: Path, offline: bool, migrate_cases: bool) -> None:
    """Stage matching runner/assets/lock for a separate reviewable upgrade PR."""
    ws = Workspace.load()
    bundle = _read_bundle(runner_wheel)
    wheel = runner_wheel.resolve()
    rel = f"vendor/{wheel.name}"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    current = ws.config.get("runner", {})
    if current.get("version") == bundle.version and current.get("sha256") != digest:
        raise click.ClickException(
            "Changed runner bytes require a new distribution version; rebuild with a version bump"
        )
    if current.get("version") != bundle.version:
        notes = bundle.assets.get("upgrade-notes.md")
        if notes is None:
            notes = (Path(__file__).parent / "assets/upgrade-notes.md").read_bytes()
        click.echo(f"Upgrading {current.get('version', 'unknown')} -> {bundle.version}")
        click.echo(release_notes(notes.decode("utf-8"), current.get("version"), bundle.version))
    marker = re.sub(
        r"(?ms)^\[runner\][ \t]*(?:#[^\n]*)?(?:\n|\Z).*?(?=^\[|\Z)",
        "",
        (ws.root / "repo2test.toml").read_text(),
    )
    runner_config = _set_table(
        "",
        "runner",
        {
            "wheel": rel,
            "version": bundle.version,
            "sha256": digest,
            **bundle.provenance,
        },
    )
    project = (ws.root / "pyproject.toml").read_text()
    project, count = re.subn(r'apitest==[^"\s]+', f"apitest=={bundle.version}", project)
    if count != 1:
        raise click.ClickException("Expected one pinned apitest dependency in pyproject.toml")
    project = _set_table(project, "tool.uv.sources", {"apitest": "placeholder"})
    project = project.replace('apitest = "placeholder"', f"apitest = {{path = {json.dumps(rel)}}}")
    # Resolve before changing this workspace. A failed lock leaves it untouched.
    with tempfile.TemporaryDirectory(prefix="repo2test-upgrade-") as tmp:
        staged = Path(tmp)
        (staged / "vendor").mkdir()
        shutil.copy2(wheel, staged / rel)
        (staged / "pyproject.toml").write_text(project)
        if (ws.root / "uv.lock").is_file():
            shutil.copy2(ws.root / "uv.lock", staged / "uv.lock")
        _run(["uv", "lock", *(["--offline"] if offline else [])], cwd=staged)
        bundle.install_assets(staged)
        migrations = {}
        if migrate_cases:
            staged_tests = staged / "tests"
            shutil.copytree(ws.path("tests"), staged_tests)
            _run(
                [
                    "uv",
                    "run",
                    "--locked",
                    *(["--offline"] if offline else []),
                    "apitest",
                    "migrate",
                    str(staged_tests),
                ],
                cwd=staged,
            )
            for path in staged_tests.rglob("*.yaml"):
                if is_case_file(path):
                    destination = ws.path("tests") / path.relative_to(staged_tests)
                    if path.read_bytes() != destination.read_bytes():
                        migrations[destination] = path.read_bytes()
        if wheel != ws.root / rel:
            shutil.copy2(wheel, ws.root / rel)
        for old_wheel in (ws.root / "vendor").glob("apitest-*.whl"):
            if old_wheel != ws.root / rel:
                old_wheel.unlink()
        for folder in (".claude/skills/repo2test-workspace", "tools/entry"):
            dest = ws.root / folder
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(staged / folder, dest)
        shutil.copy2(staged / "tools/install_entry.py", ws.root / "tools/install_entry.py")
        shutil.copy2(staged / "uv.lock", ws.root / "uv.lock")
        (ws.root / "pyproject.toml").write_text(project)
        (ws.root / "repo2test.toml").write_text(marker)
        (ws.root / "runner.toml").write_text(runner_config)
        bundle.protect_checkout(ws.root)
        for name in ("tests", "coverage", "defects"):
            directory = ws.path(name)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / ".gitkeep").touch(exist_ok=True)
        for path, content in migrations.items():
            path.write_bytes(content)
        for owners in (ws.root / ".github/CODEOWNERS", ws.root / "CODEOWNERS"):
            if owners.is_file():
                match = re.search(r"(?m)^/vendor/\*\.whl\s+([^\n]+)$", owners.read_text())
                if match:
                    _protect_runner(ws.root, match.group(1), owners)
    click.echo(
        "Upgrade staged. Review wheel/lock with the designated runner maintainer; "
        "run uv sync --locked and apitest validate --base <base-ref>."
    )


@workspace.command("doctor")
@click.option(
    "--sync", is_flag=True, help="Also synchronize locked dependencies (may use network)."
)
def doctor(sync: bool) -> None:
    """Check the pinned wheel, assets, lock and writable workspace."""
    ws = Workspace.load()
    problems = []
    try:
        with tempfile.TemporaryFile(dir=ws.root):
            pass
    except OSError:
        problems.append("workspace is not writable; configure the host's additional directory")
    problems.extend(ws.runner_problems())
    problems.extend(_legacy_layout_problems(ws))
    problems.extend(tree_problems(ws.path("tests")))
    if not (ws.root / "uv.lock").is_file():
        problems.append("uv.lock missing; resolve dependencies before generation")
    synced = False
    if sync and not problems:
        try:
            _run(["uv", "sync", "--locked"], cwd=ws.root)
            synced = True
        except click.ClickException as exc:
            problems.append(
                f"dependency synchronization failed ({exc.message.strip()[-400:]}); check network "
                "or package-index access and uv cache permissions. Codex needs "
                '--add-dir "$(uv cache dir)" and, for downloads, '
                "-c sandbox_workspace_write.network_access=true"
            )
    _dump({"workspace": str(ws.root), "problems": problems, "synced": synced})
    if problems:
        raise SystemExit(2)


@workspace.command("configure")
@click.option("--service", required=True)
@click.option("--url", required=True)
@click.option("--profile", "name", default="local")
@click.option("--environment", type=click.Choice(["non-production", "production"]), default=None)
def configure(service: str, url: str, name: str, environment: str | None) -> None:
    """Store a service URL in workspace-local ignored configuration."""
    from urllib.parse import urlsplit

    if not re.fullmatch(r"[a-z][a-z0-9_-]*", service) or not re.fullmatch(r"[\w-]+", name):
        raise click.ClickException("Use a service/profile slug without path separators")
    if (
        urlsplit(url).scheme not in {"http", "https"}
        or not urlsplit(url).netloc
        or any(c in url for c in "\n\r'\" ")
        or urlsplit(url).username
        or urlsplit(url).password
    ):
        raise click.ClickException("A valid HTTP service URL is required")
    ws = Workspace.load()
    key = re.sub(r"\W", "_", f"{name}_{service}_URL").upper()
    path = ws.path("profiles") / f"{name}.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    if environment is not None:
        data["environment"] = environment
    data.setdefault("services", {}).setdefault(service, {})["base_url"] = "${" + key + "}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    env = ws.root / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() != key]
    env.write_text("\n".join([*lines, f"{key}='{url}'"]) + "\n")
    env.chmod(0o600)
    click.echo(f"Configured {service} in {name}; URL stored in ignored .env as {key}")


@click.command("preflight")
@click.option("--profile", "name", default="local")
@click.option("--id", "ids", multiple=True, help="Check only selected path prefixes.")
def preflight(name: str, ids: tuple[str, ...]) -> None:
    """Report missing prerequisites without calling services."""
    from apitest.selector import resolve_selector

    ws = Workspace.load()
    profile = load_profile(name, ws.path("profiles"), resolve=False)
    selected = {
        node.split("::", 1)[0]
        for selector in ids
        for node in resolve_selector(selector, tests_root=ws.path("tests"))
    }
    results = []
    for path, case in _cases(ws.path("tests"), selected if ids else None):
        _, missing = prepare_profile(case, profile, scope_dir=path.parent)
        results.append({"case": str(path.relative_to(ws.root)), "blockers": missing})
    _dump(results)
    if any(r["blockers"] for r in results):
        raise SystemExit(2)


@click.command("reconcile")
@click.option(
    "--source", "source_path", type=CallerPath(exists=True, path_type=Path), required=True
)
@click.option(
    "--pattern",
    "patterns",
    multiple=True,
    help="Python regex of a route registration, for a framework without a built-in scan.",
)
@click.option(
    "--include",
    multiple=True,
    help="Files --pattern scans: '*.ts' at any depth, or a glob inside the source "
    "such as 'src/**/*.go'.",
)
def reconcile_cmd(source_path: Path, patterns: tuple[str, ...], include: tuple[str, ...]) -> None:
    """Find route registrations missing from the inventory."""
    if bool(patterns) != bool(include):
        raise click.UsageError("--pattern and --include must be given together")
    try:
        result = reconcile(
            source_path, Workspace.load().path("coverage"), patterns=patterns, include=include
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _dump(result)
    if result["discovery_gaps"] or result.get("pattern", {}).get("unmatched_evidence"):
        raise SystemExit(2)


@click.command("coverage")
@click.option("--results", type=CallerPath(exists=True, path_type=Path), default=None)
def coverage_cmd(results: Path | None) -> None:
    """Show authored, pending and verified coverage."""
    ws = Workspace.load()
    cases = [c.model_dump(mode="json", by_alias=True) for _, c in _cases(ws.path("tests"))]
    observed = json.loads(results.read_text()) if results else None
    if observed and any(
        observed.get("run", {}).get(key) != ws.identity()[key]
        for key in ("workspace_commit", "workspace_fingerprint")
    ):
        raise click.ClickException("Results do not match the current workspace contents/commit")
    try:
        view = coverage_view(ws.path("coverage"), cases, observed)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _dump(view)


@click.command("migrate")
@click.argument("path", default="tests", type=CallerPath(exists=True, path_type=Path))
@click.option("--check", is_flag=True, help="Report changes without writing.")
def migrate(path: Path, check: bool) -> None:
    """Validate v1 cases without rewriting them."""
    changed = []
    for case_path, _ in _cases(path):
        before = case_path.read_text()
        after = migrate_case(before)
        if before != after:
            changed.append(str(case_path))
            if not check:
                case_path.write_text(after)
    _dump({"changed": changed, "written": not check})
    if changed and check:
        raise SystemExit(1)


@click.command("defects")
def defects() -> None:
    """Generate a defect view from case-local strict markers."""
    ws = Workspace.load()
    _dump(
        [
            {
                "case": str(path.relative_to(ws.root)),
                "source_commit": case.source_commit,
                **marker.model_dump(exclude_none=True),
            }
            for path, case in _cases(ws.path("tests"))
            for marker in case.known_defects
        ]
    )


def register(cli: click.Group) -> None:
    for command in (workspace, preflight, reconcile_cmd, coverage_cmd, migrate, defects):
        cli.add_command(command)


def _legacy_layout_problems(ws: Workspace) -> list[str]:
    """Pieces an upgrade by an older runner CLI leaves behind (see upgrade notes)."""
    missing = []
    if not (ws.root / "runner.toml").is_file():
        missing.append("runner.toml")
    attributes = ws.root / ".gitattributes"
    if not attributes.is_file() or "# repo2test pinned assets" not in attributes.read_text():
        missing.append(".gitattributes pinned-asset rules")
    placeholders = []
    for name in ("tests", "coverage", "defects"):
        directory = ws.path(name)
        # Git drops empty directories, including ones holding only empty directories.
        populated = directory.is_dir() and any(
            p.is_file() and p != directory / ".gitkeep" for p in directory.rglob("*")
        )
        if not populated and not (directory / ".gitkeep").is_file():
            placeholders.append(f"{name}/.gitkeep")
    for owners in (ws.root / ".github/CODEOWNERS", ws.root / "CODEOWNERS"):
        if not owners.is_file():
            continue
        text = owners.read_text()
        runner_owner = re.search(r"(?m)^/vendor/\*\.whl\s+(.+)$", text)
        # Only the rule generated for the runner owner is legacy; team rules stay valid.
        if runner_owner and f"/repo2test.toml {runner_owner.group(1).strip()}" in text.splitlines():
            missing.append(f"{owners.relative_to(ws.root).as_posix()} still owns /repo2test.toml")
    problems = [
        f"missing {path}: create it so Git keeps the empty directory" for path in placeholders
    ]
    if missing:
        problems.append(
            "incomplete workspace layout (" + ", ".join(missing) + "); rerun `workspace "
            "upgrade --runner-wheel <the vendored wheel>` with the current framework CLI"
        )
    return problems
