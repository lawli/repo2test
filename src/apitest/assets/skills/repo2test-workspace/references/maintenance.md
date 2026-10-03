# Workspace maintenance

The default test branch targets the reference environment. When that environment replaces
V with V+1, preserve the prior suite as `<target>@<V>` before updating the reference; old
environments run that tag's worktree and its own runner. Hotfix branches can carry old-line
regressions and cherry-pick applicable changes forward. Never guess that deployment HEAD
equals the newest default-branch commit.

If a deployed ref is unavailable, record deployment time and obtain the latest eligible
first-parent commit from a known release/default branch with
`git -C <business> rev-list --first-parent -1 --before=<deployment-time> <branch>`.
This reads local history; never fetch or checkout in the business repository. The ref
must resolve in the local business checkout: if a known ref is missing there, ask the user
to make it available instead of substituting another ref or an estimate. If
neither ref nor time can be established, record a default-branch estimate explicitly in
the reference marker and final report. `workspace init` requires an explicit `--ref`;
add `--estimated-because <evidence>` for a fallback. Do not present the estimate as verified deployment.

When the reference upgrades, tag the reviewed old test commit before editing:
`git tag '<target>@<old-version>' <old-test-commit>`. To run an old environment or a rollback,
create a separate test worktree with `git worktree add <old-workspace> '<target>@<version>'`,
then run `uv sync --locked` and the selected suite there. For old-line fixes, branch from
that tag, validate against its anchor, and cherry-pick applicable case changes forward;
re-review source anchors on the new line. Tags and pushes follow the user's requested
maintenance scope; test generation authorizes only the scenario PR delivery described in
[the workspace skill](../SKILL.md), never tags.

`apitest workspace source --ref <ref>` makes an isolated source checkout. Review changed
evidence before updating `source_commit`; updating an anchor alone does not prove review.
`apitest workspace reference --ref <deployed-ref>` updates the reference marker without
rewriting existing anchors and lists each coverage row whose evidence files changed since
that row's own `source_commit`, with the cases that claim it; review those before
re-anchoring them. A row stays listed until it is re-anchored; a row whose anchor is not
a full commit id, or is missing from history, is always listed. A `HEAD` ref is recorded as
the commit it resolves to. Use `--estimated-because <evidence>` for deployment fallbacks.
Use `apitest validate --base <base-ref>` on the merged candidate. Configure the generated
test-repository CI as required, with up-to-date branches or a merge queue/train, and retain
default-branch push validation. Business CI remains unchanged.

Upgrade runner wheel, pinned workspace skill and shipped user-entry sources together in a
separate PR. Install/update the user entry from `tools/install_entry.py` only when requested.
Use `apitest workspace upgrade --runner-wheel <wheel> [--migrate]` to stage matching assets
with a new distribution version whenever wheel bytes change. Reapplying the identical wheel
may restore pinned assets without a version bump. If the upgrade reports that changed runner
bytes need a new version, stop and obtain a correctly versioned bundle; never edit
`runner.toml` to bypass the check. Resolve the new lock before replacing
the current distribution.
Always upgrade with the new framework checkout's CLI and `--workspace <path>`: an older
workspace CLI cannot apply newer layout changes, and the new CLI prints the migration notes.
`runner.toml` pins the wheel hash; legacy inline `[runner]` metadata remains readable.
Upgrade separates that metadata from routine references and adds Git attributes for exact
asset bytes and placeholders for empty directories. `uv sync --locked` consumes the lock.
`workspace doctor` and `validate` compare all distributed skill and entry files with that
wheel. Restore altered assets from the reviewed wheel or upgrade the bundle together.
`workspace doctor` also reports a layout left incomplete by upgrading with an older CLI;
rerun the upgrade with the new CLI and the vendored wheel to finish it.
The designated runner maintainer, distinct from the PR author, reviews distribution changes,
including the wheel, lock, skills, entry tools, runner configuration and CI protection;
enable the generated CODEOWNERS protection on the git platform.

`apitest migrate` validates current v1 cases without rewriting their contents: comments,
quoting, formatting and manual edits remain intact. There is no v1 schema transition to apply.
Future schema migrations must make only necessary versioned edits. For the runner-upgrade
exception to anchor checking, the proposed case bytes must equal replaying migration on
the base version and preserve `source_commit`. Helper renames and business edits do not get
this exception. Keep unknown/unsupported schemas as explicit errors rather than guessing.
Migration only transforms supported schema structure. Existing `known-failure` tags do
not become strict markers automatically: re-review their assertions against the correct
contract, then add scoped `known_defects` and remove assertions that pin broken behavior.
