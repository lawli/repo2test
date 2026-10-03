---
name: repo2test
description: Create API end-to-end test cases for a repository (为这个 repo 创建 test cases) or a specific feature (用 repo2test 创建 test cases). Use for these authoring requests even when repo2test is not named, and for running or extending an existing repo2test suite. Covers black-box HTTP API tests for backend services; not for unit, UI or load tests.
---

Preserve the user's business repository path, explicit feature, relevant conversation,
and requested action before changing directories. Creation means authoring and static
validation; execution requires a request to run. If the request is for unit, component,
UI or load tests, say that repo2test does not apply and stop.

Run `python3 scripts/locate.py --start <current-directory>` relative to this skill's
directory and act on the JSON it prints:
- `ready`: use the reported `workspace` and `business`.
- `configuration-required`: if `writable` is false, give the host-specific step below; if
  `business` is null, ask only for the business repository path.
- `workspace-missing` (exit 2): look for the existing `<repository>-e2e` in the same
  group/org with an available Git platform tool, such as `gh` or `glab`. Otherwise confirm
  from the business build file and application entry that the repository serves an HTTP
  API, report a repository without one as out of scope, and ask whether to use an existing
  workspace or initialize one at `suggested`.
  To initialize, follow [setup](references/setup.md).
- Any other output: report it verbatim and stop.
Preserve existing files and do not silently create a remote.

Before generation, verify the selected workspace is writable and run
`uv sync --locked` there. If permissions or dependency access prevent this, give a
concrete host-specific next step. Codex: relaunch with `--add-dir <workspace>
--add-dir "$(uv cache dir)"`, adding `-c sandbox_workspace_write.network_access=true`
when uv must download packages. Claude Code: relaunch with `--add-dir <workspace>`.
Installation does not grant those permissions.

Run `uv run --locked apitest workspace doctor` before loading authoring rules.
Proceed only when it reports no problems. It checks the wheel and pinned skill/entry
assets, the workspace layout and the test tree; report each problem with its recovery
step, such as restoring the reviewed bundle or a deliberate runner upgrade.

Read the exact `skill` path in the workspace's `[paths]` table, then follow that pinned
skill with the saved request context. Do not substitute the globally installed version
for a workspace's pinned authoring rules. If target information is absent or ambiguous,
ask only for the missing location. Stop routing once the pinned skill is loaded.
