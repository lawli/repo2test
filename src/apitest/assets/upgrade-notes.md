# Runner compatibility notes

Review the releases crossed by this upgrade before executing existing cases.
Upgrading or migrating never authorizes requests to a business service.
After upgrading, run `uv sync --locked`, `apitest workspace doctor`, and static validation.

Upgrade with the new framework checkout's CLI:
`uv run --locked apitest --workspace <workspace> workspace upgrade --runner-wheel <wheel>`.
An older workspace CLI cannot apply newer layout changes. `workspace doctor` reports such an
incomplete layout; rerunning the upgrade with the new CLI and the vendored wheel repairs it.

## 0.2.1

- `environment` must be explicit in the selected profile; it is not inherited.
- Python helpers are treated as writes. Unknown helpers require non-production, isolation
  and cleanup.
- `known_defects.ref` must be a tracker ID (`PROJ-123`, `#123`, `owner/repo#123`), an HTTP
  URL or an existing `defects/*.md` record. Move other local defect notes into `defects/`.
- `verify.db.sql` supports one read-only SELECT using supported built-ins. Verification
  has a separate connection and cannot see setup temporary tables or uncommitted changes.
  Move unsupported SQL to a guarded helper.

## 0.2.2

- Case `mutates: false` exempts read-only POSTs only in execute steps. Move an evidenced
  read-only login/search there; setup and teardown write requests remain guarded.
  A read-only case cannot also declare teardown actions or `data.cleanup`.
- Runner provenance moves to `runner.toml` so routine reference updates do not require
  runner-maintainer approval. The loader still accepts legacy inline metadata.
- Git attributes preserve exact pinned asset bytes across platform line-ending settings.
  Empty tests, coverage and defects directories are retained when committing and cloning.
  Existing clones made with `core.autocrlf=true` keep converted copies of unchanged pinned
  files; run `git checkout -- .claude tools` once after pulling the upgrade.
- Read-only helper exceptions used a reviewed `helper-trust.toml` policy. 0.2.3 removes it.

## 0.2.3

- `helper-trust.toml` and `workspace helper-review` are removed; delete the file. The
  upgrade removes its generated CODEOWNERS line. Cases that call Python helpers run only on
  profiles marked `environment: non-production`, even when read-only.
- On a non-production profile, a helper case that declares `mutates: false` needs no
  isolation, cleanup or teardown. Declare it only when every helper really just reads.
- `mutates: false` is rejected together with setup writes (non-GET setup steps or DB,
  Redis or RabbitMQ seeding), execute PUT/PATCH/DELETE or `data.resources`. Move a
  read-only login to the execute steps.
- `apitest run` no longer lets pytest import workspace Python: earlier runners imported
  `conftest.py` and package `__init__.py` files on every run, bypassing the write guard.
  `validate`, `run` and `doctor` reject such files under `tests/`, and other Python
  outside `_helpers` or `_shared`; move shared code into `_helpers` modules called from
  cases. They also reject symlinked test directories, whose cases were never validated.
- Coverage rows with status `gap`, `blocked` or `needs-review` must state a `reason`.
- Write expected values that begin with `@` as `@@value`; `@value` is a matcher.
- Relative paths given to `apitest` commands resolve from the directory where you run them.
- `workspace reference` lists coverage rows whose evidence changed since each row's
  anchor; a row whose anchor is not a full commit id is always listed. `HEAD` is recorded
  as the commit it resolves to. Upgrades remove the previous wheel from `vendor/`.
- The pinned skill now runs `uv sync --locked` and `workspace doctor` before authoring or
  running when a session starts in the workspace. Existing `AGENTS.md` files are kept; new
  ones carry the same precondition.
- Feature requests limit new cases and coverage fragments to the selected feature and
  leave unrelated fragments unchanged.
- A contract conflict found in source is recorded as a suspected defect, not yet
  reproduced; `known_defects` is added only after an execution reproduces it.
- New workspaces anchor `reports/` and `.source/` in `.gitignore` to the workspace root.
  Existing workspaces keep their file; anchor those entries if a scenario uses either name.
- Profiles cannot hold literal credentials. A profile fails to load when any file in its
  `extends` chain has a URL password or sensitive URL parameter that is not exactly one
  `${VAR}`, or a header, `test_data` or other string or list item without `${VAR}` directly
  under a key containing `authorization`, `cookie`, `token`, `secret`, `password`,
  `passwd`, `pwd`, `passphrase`, `apikey`, `credential`, `signature` or `privatekey`.
  Run `apitest validate`, move each reported value to the ignored `.env` or a CI secret
  under the suggested name, and reference it.
  Direct `pytest` runs now load the workspace `.env` as `apitest run` does.

## 0.2.4

- `apitest validate` checks every evidence citation in the coverage fragments when the
  source checkout of the row's commit is present under `.source/`: the file must be inside
  the checkout, and the cited line must exist and not be blank. Correct the citations it
  reports. Without a checkout, as in CI, nothing is checked.
- `apitest reconcile` adds `cross_check` and a `scanner` on each mapping to its output, and
  the `scope` text changed. The exit code is unchanged unless `--pattern` is used.
- Existing cases need no change.
