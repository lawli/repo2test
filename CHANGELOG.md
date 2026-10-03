# Changelog

## 0.2.4

No action is needed in existing workspaces, with one exception: where a source checkout is
present, `apitest validate` now rejects evidence citations that checkout does not support.
The upgrade prints this as the 0.2.4 compatibility note.

- Known-defect (`XFAIL`) results keep the HTTP exchange log in the HTML and JUnit reports.
- Failure text in the console and in the HTML and JUnit reports names the case by its path
  inside the workspace instead of its absolute path on the machine that ran it.
- A missing `extract` value fails at `extract.<name>`, and `known_defects.at` accepts that
  location, so a step that only extracts can carry a strict marker.
- `known_defects.at` accepts `response` for an execute step whose connection the service
  drops without a response. Only a marker with exactly that `at` matches; an unreachable
  service, a timeout and a marker without `at` still give `ERROR`.
- A teardown step may list the statuses it accepts, `assert: {status: [204, 404]}`. Setup
  and execute steps still take one status.
- A failing case with `expectation: pending` is labelled `pending expectation not met` and
  counted in `totals.pending_fail` of `summary.json`. It still fails the run.
- A cleanup request that cannot be built because a step did not return a value names that
  step and the status it answered, instead of a template-engine error. The outcome is
  unchanged: it is still a cleanup failure.
- The skills accept any repository that serves an HTTP API instead of stopping on one that
  is not Spring Boot. Spring Boot and FastAPI are the verified stacks; a run on another
  framework says so in its report. Declarative contract evidence now names typed request
  and response models and the status code and response model a route declares.
- `apitest reconcile` also scans FastAPI route decorators, and `--pattern <regex>` with
  `--include <glob>` scans the route syntax of any other framework. The output reports
  `cross_check`: the scans that found registrations (`spring`, `fastapi`, `pattern`, joined
  with `+`), or `unavailable` when nothing matched and an empty gap list proves nothing.
  Each mapping names its `scanner`; a supplied pattern is echoed with its file and match
  counts and with `unmatched_evidence`, the cited route lines no scan found. A run with
  `--pattern` exits 2 while that list is not empty, so a pattern that is too narrow cannot
  pass as a clean cross-check. Without `--pattern` the exit code is unchanged.
- `apitest validate` checks every evidence citation against the source checkout of its
  commit when that checkout exists: the file must be inside it, and the line must exist and
  not be blank. Without a checkout, as in CI, nothing is checked.
- Evidence has a new kind, `test`: a test checked into the business repository that asserts
  the behavior. It can confirm an expectation where no requirement or declarative
  constraint settles it, and never overrules one. `apitest coverage` reports `test_backed`,
  the confirmed rows that rest on such tests alone.
- The pinned references document the assertion matchers, how a case references external
  test data, what `${...}` renders and which fields are literal, and how coverage paths
  and evidence locations are written.
- Every CLI command describes itself in `--help`.
- The package metadata carries authors, keywords and project URLs.

## 0.2.3

0.2.2 workspaces: delete `helper-trust.toml` now, even before upgrading. The 0.2.2 runner
then treats every helper as a write, which closes the approval bypasses listed below.

- **Breaking:** profiles refuse literal credentials. Loading fails when any file in the
  `extends` chain has a URL password or sensitive URL parameter that is not exactly one
  `${VAR}`, or a string or list item without `${VAR}` directly under a key containing
  `authorization`, `cookie`, `token`, `secret`, `password`, `passwd`, `pwd`, `passphrase`,
  `apikey`, `credential`, `signature` or `privatekey`. `apitest validate` checks every
  profile file. Errors name the field and a suggested variable, never the value or a YAML
  source line, and the CLI prints profile errors without a traceback. Direct `pytest` runs
  inside a workspace load its `.env`. The example profiles now use
  `<PROFILE>_<SERVICE>_DB_USER`/`_DB_PASS` and `<PROFILE>_MQ_USER`/`_MQ_PASS`; rename
  these keys in an existing `.env`.
- Remove the reviewed read-only helper policy (`helper-trust.toml`, `workspace helper-review`):
  name collisions, nested directories and uncommitted policy files could bypass it. Cases
  that call Python helpers now run only on non-production profiles; there, `mutates: false`
  waives isolation, cleanup and teardown.
- Reject `mutates: false` together with setup writes, execute PUT/PATCH/DELETE or
  `data.resources`, and list every offending step or helper in write blockers.
- Stop pytest importing workspace `conftest.py`, package `__init__.py` and test modules in
  `apitest run`; they bypassed the write guard. `validate`, `run` and `doctor` reject
  test-tree Python outside `_helpers`/`_shared` and symlinked test directories.
- Skip, rather than fail, cleanup steps whose resources were never requested; a resource
  that may exist without its identifier, including one whose response failed to decode,
  still fails cleanup and lists residual data. A skip names the other context variables
  the step referenced.
- The pinned workspace skill and new `AGENTS.md` require `uv sync --locked` and
  `workspace doctor` before authoring or running when a session starts inside the
  workspace.
- Feature requests limit new cases and coverage fragments to the selected feature;
  source-found contract conflicts are recorded as suspected defects until reproduced.
- Record the failed setup step, accept `@@value` literals in assertions, resolve relative
  CLI paths from the caller's directory, and require a reason on gap/blocked/needs-review rows.
- Replace, not merge, the user entry on `--update`, restoring the previous entry if the
  swap fails (or naming where it is saved) and removing what an interrupted update left;
  give concrete Codex directory and network flags; report `uv sync` failures in `workspace
  doctor --sync`.
- `workspace reference` lists rows whose evidence changed since each row's own anchor and
  records `HEAD` as its commit; upgrades print only the crossed releases' notes and remove
  the previous wheel; `doctor` reports layouts left incomplete by an older CLI; new
  `.gitignore` entries are rooted.
- Accept more pure SQL functions, `SUBSTRING … FOR` and unambiguous backslash escapes in
  verification SQL; ignore editor swap and backup files when checking pinned assets.
- `workspace init` removes a partial workspace when `uv lock` fails, so the same command can
  be rerun. Unmatched selectors and invalid cases print one `Error:` line, and `preflight`
  validates only the selected cases. The entry skill stops for unit, UI or load test
  requests and maps each `locate.py` status to a next step; scenario PR delivery never
  pushes the default branch, force-pushes or creates tags.
- `assert.json` accepts the JSONPath spelling that `extract` uses: `$.data.id` for
  `data.id`, and `$` for the whole body, so a scalar response can be asserted directly.
- The pinned skill records every suspected contract conflict as a `defects/*.md` note and
  reports a `partial`, `blocked` or `complete` delivery status, checked in that order;
  pending expectations are listed but do not prevent `complete`. Scenario PRs are published
  only with a writable remote, working authentication and a known default branch, and
  commit only files the request changed. The user entry ships `references/setup.md`, which
  initializes a workspace from a trusted wheel with `uvx --from <wheel>`.

## 0.2.2

- Block setup POST writes even with `mutates: false`; reject that flag alongside cleanup.
- Scan dependencies only in rendered inputs; preserve literal metadata and uploaded bytes.
- Support reviewed read-only Python helpers through a protected policy pinned to workspace
  Python, locked dependencies and the runner. Changed inputs require a new review.
- Preserve pinned assets across Git line-ending conversion and retain empty workspace
  directories after cloning. Ignore OS metadata when checking asset drift.
- Resolve complete profile inheritance chains and report cycles.
- Derive verified coverage from authored cases and matching results, without a manual
  `authored` status update. Explicit blocked/review/gap rows remain unverified.
- Support `GROUP_CONCAT` in read-only verification SQL.
- Print compatibility notes during upgrades and separate protected runner metadata into
  `runner.toml`, leaving routine source-reference updates in `repo2test.toml`.

For the new upgrade checks, use the 0.2.2 framework CLI to upgrade an older workspace.
See the [compatibility notes](src/apitest/assets/upgrade-notes.md).

## 0.2.1

- Enforce write prerequisites for inherited profiles and Python helpers. Verification SQL
  accepts a conservative SELECT subset and runs on a separate read-only connection.
- Report cleanup failures, residual identifiers and recovery instructions in terminal,
  JSON, JUnit and HTML output, including failures to close RabbitMQ connections.
- Discover indexed and dotted test-data references before execution. Require explicit
  dependency declarations for dynamic access.
- Accept tracker IDs, issue URLs and local Markdown records for known defects.
- Preserve authored YAML during migration and compare migration exceptions byte-for-byte.
- Require complete runner metadata; detect pinned skill drift and inconsistent provenance.
  Changed wheel bytes require a new version when upgrading a workspace.
- Correct Spring route scanning around character literals and production package names.
  Require an explicit source ref and record estimated deployment references.
- Restore small scenario PRs as the skill's default delivery when a remote exists;
  retain local delivery when requested or when no remote is configured.
- Strengthen host probes with user-level cold starts, sandbox and missing-URL scenarios,
  interrupted-work recovery, all-method request recording and business-file snapshots.

Compatibility: arbitrary verification SQL now needs a guarded Python helper. Verification
cannot see uncommitted setup data or connection-local temporary tables. Workspaces missing
runner provenance must restore their pinned distribution metadata before validation passes.
