# Agent entry

The maintained authoring workflow is now a distributed skill:

- [User entry](../src/apitest/assets/entry/repo2test/SKILL.md) locates the independent test workspace.
- [Workspace skill](../src/apitest/assets/skills/repo2test-workspace/SKILL.md) defines generation and execution.
- [Authoring reference](../src/apitest/assets/skills/repo2test-workspace/references/authoring.md) defines inventory, evidence, cases and incremental updates.
- [Environment reference](../src/apitest/assets/skills/repo2test-workspace/references/environment.md) defines configuration guidance.

Use the copies shipped with the selected workspace, not this source tree's latest version.
Follow the [README](../README.md) to initialize a workspace and install its user entry.

The former prompt's automatic service execution, mandatory DB checks, arbitrary minimum
field counts and assertions that reproduce known bugs have been retired. Creation stops
at authored cases, coverage fragments, configured service URLs and static validation.
Execution requires a request to run; correct expectations remain intact when defects exist.
