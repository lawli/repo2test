---
name: repo2test-workspace
description: Author, maintain, validate, and run HTTP API test suites inside a pinned repo2test test workspace after the repo2test entry has located it.
---

Work inside the selected test workspace using its locked `uv` environment. The business
repository is a read-only source of context; preserve its branch, index and uncommitted
work. Read `repo2test.toml` and the existing coverage fragments before editing cases.

Before authoring or running, confirm the workspace is writable, then run
`uv sync --locked` and `uv run --locked apitest workspace doctor` there, unless the
repo2test entry already did so in this session. Continue only when doctor passes:
`problems` is empty (`synced` only says whether doctor ran the sync itself).
Otherwise report the problem with its recovery step. For missing access, Codex relaunches
with `--add-dir <workspace> --add-dir "$(uv cache dir)"`, adding
`-c sandbox_workspace_write.network_access=true` when uv must download packages; Claude
Code relaunches with `--add-dir` for the workspace or the business repository it cannot
reach. For drift or an incomplete layout, follow [maintenance](references/maintenance.md).

## Establish the request

An explicit repository-wide request covers all identifiable HTTP entries. A feature
request uses explicit requirements first, established conversation second, and current
diff last. Ask about scope only when these leave multiple reasonable interpretations.
The selected scope applies to both test cases and coverage fragments.
Use business working-tree changes only for scope, not as the versioned source of assertions.

Creation ends after cases, coverage fragments and static validation are delivered and
the targeted service URLs are configured. It does not authorize service calls, login,
test execution, or cleanup. If the user asks to run, read [execution](references/execution.md).

## Author

Read [authoring](references/authoring.md) for source anchoring, endpoint inventory, contract
evidence, YAML and incremental rules. Inventory every endpoint and behavior within the
selected scope before drafting individual cases. Retain those remaining rows so an
interrupted run can resume without losing its denominator.

If environment settings are missing, read [environment](references/environment.md).
Ask for missing service URLs while continuing source analysis and drafts. Keep secrets
and environment values in local ignored files or externally managed variables.

Run `uv run --locked apitest validate --profile <profile>` in the workspace. Resolve all
schema, reference, matcher, helper, fixture and anchor errors before claiming static
validation passed. Review the diff to confirm changed cases and coverage fragments stay
within the selected scope. Use `uv run --locked apitest coverage` for generated views. Report
separately: authored rows, unauthored rows (`pending`), pending expectations
(`pending_expectations`), confirmed rows that rest on repository tests alone
(`test_backed`), blocked rows, registrations `reconcile` lists as uncited
(`discovery_gaps`), rows with status `gap` (`gaps`), and suspected defects with links
to their `defects/*.md` notes; generation does not prove execution coverage. State the
delivery status, choosing the first that applies: `partial` while an in-scope `uncovered`
row can still be authored, naming the next scope; `blocked` when the remaining work needs
a user input or permission (an in-scope `blocked` row, a missing service URL, or a
validation error that needs user input), naming each item; otherwise `complete`, which
requires every in-scope row to be `authored` or a `gap`/`needs-review` row with its reason,
passing validation and configured URLs. A reason explains unfinished work but never
completes it. Pending expectations do not prevent `complete`; list each with the evidence
it needs. Examples: one batch done while other scenarios are still `uncovered` is
`partial`; every case written but the service URL missing is `blocked`; every row authored
except one `blocked` row waiting for a refund sample is `blocked`; every row authored, one
dynamic route recorded as a `gap` and two pending expectations is `complete`.

When updating the runner or reference version, read [maintenance](references/maintenance.md).
Keep changes reviewable by scenario and reuse existing cases instead of bulk replacement.
When the test repository has a remote, deliver small scenario PRs by default, with remaining
inventory rows visible: a new branch per scenario, its commits, a push of that branch and a
PR against the default branch. This default authorizes nothing else: never push the default
branch, force-push, create tags or create a remote. Publish only when the remote is
writable, authentication works and the default branch is known, and commit only files this
request changed. If publishing fails, keep the local branch and report the missing
prerequisite. Honor an explicit local-only request.
Use draft PRs when the reference is not yet deployed. Without a remote, deliver the local
diff and report the repository setup needed for PR delivery.
