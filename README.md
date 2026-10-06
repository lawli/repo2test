# repo2test

**API tests built to question your code, not just copy it.** Claude Code or Codex builds a
suite from your backend repo and adds end-to-end tests when you describe a change.

[![CI](https://github.com/lawli/repo2test/actions/workflows/ci.yml/badge.svg)](https://github.com/lawli/repo2test/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/lawli/repo2test)](https://github.com/lawli/repo2test/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](pyproject.toml)

[A real run](#a-real-run) · [Why](#why-repo2test) · [Get started](#get-started) ·
[Scope](#scope) · [Reference](#reference) · [中文](README.zh-CN.md)

## A real run

Claude Code was asked to create happy-path and edge-case API tests for
[spring-petclinic-rest](https://github.com/spring-petclinic/spring-petclinic-rest), a
Spring Boot API with 37 documented operations, and then to run them against a local
instance.

The agent inventoried 44 endpoints and 307 behaviors, wrote 202 cases, and recorded 13
conflicts between the implementation and its own OpenAPI contract. The suite runs in about
five seconds:

| Result | Cases |
|---|---|
| Passed | 109 |
| Reproduced a recorded contract conflict (`XFAIL`) | 92, across 11 defect notes |
| Failed on a question the contract does not settle | 1 |

Where it had nothing to go on, it did not guess: 82 behaviors are listed as blocked, mostly
role checks that need test identities, and 23 as gaps, each with its reason.

![HTML report of the run](docs/assets/petclinic-report.png)

The whole result is a Git repository you can read:
[spring-petclinic-rest-e2e](https://github.com/lawli/spring-petclinic-rest-e2e).

<details>
<summary>A generated case: it asserts the contract and marks the known defect</summary>

```yaml
schema: v1
name: Get an owner with a negative id is a bad request
description: >-
  Read-only. ownerId has minimum 0 (openapi.yml:221) and the operation documents 400 with a
  ProblemDetail (openapi.yml:242-247), so -1 must be answered with 400. The exception handler maps
  only request-body validation to 400 (ExceptionControllerAdvice.java:117) and everything else to
  500 (line 80). Known defect, reproduced:
  defects/invalid-request-values-not-answered-with-400.md.
service: petclinic
source_commit: 4cd8e1b0cd42578e882247d8801f6be5d402f118
covers:
  - {key: "petclinic|GET|/api/owners/{ownerId}|validation|negative-id", variant: default}
known_defects:
  - ref: defects/invalid-request-values-not-answered-with-400.md
    step: "get owner -1"
    at: status
steps:
  - name: get owner -1
    request:
      method: GET
      path: /api/owners/-1
    assert:
      status: 400
      json:
        status: 400
        title: "@type string"
        detail: "@type string"
        type: "@type string"
        timestamp: "@iso8601"
        schemaValidationErrors: "@type array"
```

</details>

<details>
<summary>The defect note that case points to (excerpt)</summary>

```markdown
# Request values outside the declared schema are not answered with 400, except body bean-validation failures

- Status: reproduced on 2026-10-04 against the `local` profile for all 25 operations.
- Service: `petclinic`
- Source commit: `4cd8e1b0cd42578e882247d8801f6be5d402f118`

### GET /api/owners/{ownerId}

- contract: src/main/resources/openapi.yml:219 — "type: integer"
- contract: src/main/resources/openapi.yml:221 — "minimum: 0"
- contract: src/main/resources/openapi.yml:242 — "400:"

| Request | Expected (contract) | Actual |
|---|---|---|
| `GET /api/owners/-1` | 400 | 500, title `ConstraintViolationException` |
| `GET /api/owners/abc` | 400 | 500, title `MethodArgumentTypeMismatchException` |
```

</details>

<details>
<summary>Coverage counters after the run</summary>

```json
{
  "pending": 105,
  "authored": 202,
  "pending_expectations": 15,
  "blocked": 82,
  "needs_review": 0,
  "gaps": 23,
  "verified": 95,
  "test_backed": 0
}
```

`pending` counts behaviors without a case: the 82 blocked rows and the 23 gaps. `verified`
counts only cases that passed against a confirmed expectation. `test_backed` counts
confirmed expectations whose only evidence is a test of the repository itself.

</details>

**The same request on FastAPI.** On
[full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template), a
FastAPI project with PostgreSQL and token login, the agent inventoried 28 endpoints and 148
behaviors and wrote 90 cases. The run passed 84 and reproduced six recorded conflicts. Half
of the covered behaviors, 71 of 144, are marked pending rather than confirmed, because
their expected responses appear only in handler code, which repo2test does not take as a
contract. Result:
[full-stack-fastapi-template-e2e](https://github.com/lawli/full-stack-fastapi-template-e2e).

## Why repo2test

- **Expectations come from contracts, not from current behavior.** Every covered behavior
  records its evidence: the file and line of a requirement, an OpenAPI entry or a validation
  annotation, or failing those a test the repository itself checks in, counted separately.
  Without evidence the expectation is marked pending instead of invented.
- **Conflicts become defect notes.** When the code contradicts the contract, the case keeps
  asserting the contract and the conflict is written up with file and line on both sides.
- **Gaps stay visible.** Endpoints and behaviors are inventoried before any case is written.
  What is not covered is listed as uncovered, blocked or a gap, with the reason.
- **Writes are blocked outside non-production.** A case that changes data runs only against
  a profile you have declared non-production, with isolated data and executable cleanup.
- **The suite outlives the agent.** The output is a plain Git repository of YAML cases with
  a pinned runner. Teammates and CI run it with Python and uv: no agent, and no checkout of
  this framework.

## Get started

Paste this into Claude Code or Codex:

```text
Install the repo2test skill by following https://github.com/lawli/repo2test#installation. Installation only.
```

Then start the agent in your backend repository and ask:

```text
/repo2test Create happy-path and edge-case API tests for this repository. Use the local profile. Do not run the tests.
```

In Codex the skill is `$repo2test`. On first use the agent prepares a test repository beside
your code: it downloads the released runner, works out the settings from your repository
and asks you to confirm them once. See
[Create your first test workspace](#create-your-first-test-workspace).

## Scope

HTTP APIs of a backend repository, including sequential flows across services in one
repository. It is not for unit, UI or load tests.

Verified on Spring Boot and FastAPI: the two runs above. The case format, the runner and
the agent's method are not tied to a framework. On another stack the agent works the same
way and says in its report that the stack is unverified. Two parts are narrower today:

- `apitest reconcile`, the mechanical cross-check of the endpoint inventory, knows the
  route syntax of Spring and FastAPI. On other stacks the agent passes the framework's
  route syntax as a pattern, and the output records the pattern and what it matched.
- Database, cache and broker checks support MySQL, Redis and RabbitMQ. Cases that only call
  the API do not need them.

If repo2test falls short on your stack, [open an issue](https://github.com/lawli/repo2test/issues).

---

## Reference

Everything below is the operating manual that agents and maintainers follow.

repo2test turns a backend repository and your feature context into maintainable API
test suites. Your coding agent discovers endpoints, designs happy paths and edge cases,
and records the evidence behind each expectation. The bundled `apitest` runner validates
and executes the generated YAML.

| Ask your agent to… | What repo2test does |
|---|---|
| Create tests for a repository | Inventories discoverable HTTP endpoints first, then generates cases in scenario batches and tracks remaining gaps. |
| Create tests for a feature | Uses your explicit request and conversation context to scope the work, then traces the relevant source and contracts. |
| Set up or run a suite | Guides service URL and required dependency configuration, checks prerequisites, and reports execution and cleanup outcomes. |

**Creating tests produces files and static validation. Running them is a separate request.**

[When to use](#when-to-use) · [Installation](#installation) ·
[First workspace](#create-your-first-test-workspace) · [Usage](#use-with-your-agent) ·
[Agent rules](#rules-for-agents) · [Best practices](#best-practices) ·
[Documentation](#documentation)

## When to use

Use repo2test for black-box HTTP API tests of a backend repository: generating
happy-path and edge-case cases for the whole repository or one feature, extending or
maintaining an existing repo2test suite, and running it. Spring Boot and FastAPI are the
verified stacks.

It does not apply to unit or component tests, UI or browser tests, load tests or non-HTTP
interfaces. Agents confirm from the business build file and application entry that the
repository serves an HTTP API before creating a workspace.

## How it works

The **business repository** is the application whose APIs you want to test. Each business
repository has a separate **test workspace**, conventionally named `<repository>-e2e`:

```text
/work/
├── orders/          # Business source, read-only to repo2test
└── orders-e2e/      # Cases, coverage, configuration and bundled runner
```

The installed `repo2test` entry locates that workspace and loads its pinned authoring
skill. The agent analyzes an isolated checkout of the selected source commit and writes
test artifacts into the test workspace.

Each test repository carries its runner wheel, skill and dependency lock. A teammate or
CI worker can clone it and run an existing suite with Python and uv; executing tests
requires neither a code agent nor a checkout of this framework.

**Current scope:** HTTP APIs, verified on Spring Boot and FastAPI, including sequential
flows across services in the same repository. MySQL, Redis and RabbitMQ checks are
available when needed. Coverage
is based on accessible source and contracts; conditional or dynamic routes and uncertain
expectations remain explicit gaps. All services in a workspace share one reference commit.

## Installation

### Skill installation prerequisites

- Python **3.11+** (check with `python3 --version`) and a local copy of this framework or
  an existing team test repository. Use Git if you need to clone either repository.
- Claude Code or Codex with skill support to verify that the host discovers the entry.

The installer uses only Python's standard library. Installing the entry does not require
uv, a business repository, a test workspace or service URLs. Those belong to
[first-use setup](#create-your-first-test-workspace).

**For agents handling an installation-only request:** install the entry for the requested
host and follow [verification](#verify-the-skill-installation). Stop there; workspace
creation and environment configuration are separate tasks. The installer writes to the
user's home directory, outside the project; in a sandbox, request that permission or ask
the user to run the command. If it exits with `Entry exists at <path>`, the entry is
already installed: verify it and add `--update` only when the user asked to update.

### Install from this repository

If you already have this framework checked out, use its root directory. Otherwise, clone
it outside your business repository, for example beside it:

```sh
git clone https://github.com/lawli/repo2test.git repo2test
cd repo2test
```

From the framework checkout root, run the command for your agent:

**Claude Code**

```sh
python3 src/apitest/assets/install_entry.py --host claude
```

Installs the entry at `~/.claude/skills/repo2test`. Invoke it with `/repo2test` inside
Claude Code. See the [Claude Code skills documentation](https://code.claude.com/docs/en/skills)
for host discovery rules.

**Codex**

```sh
python3 src/apitest/assets/install_entry.py --host codex
```

Installs the entry at `~/.agents/skills/repo2test`. Invoke it with `$repo2test` inside
Codex. See the [Codex skills documentation](https://developers.openai.com/codex/skills/)
for host discovery rules.

The entry is installed once per user and host. The installer copies it, so the installed
skill does not depend on this checkout afterwards. Continue to
[verify the installation](#verify-the-skill-installation).

### Install from an existing team test repository

If your team already maintains `orders-e2e`, you can install directly from a local clone
of that test repository. Its installer and entry sources are included:

```sh
cd /work/orders-e2e
python3 tools/install_entry.py --host claude
# For Codex, use --host codex instead.
```

### Verify the skill installation

The installer should exit successfully and print `Installed <absolute-entry-path>.`,
followed by a note that existing workspaces remain pinned. Its second line says how to give
the host access to a test workspace (`--add-dir`); that applies once a workspace exists and
needs no action during installation. Check that `SKILL.md`,
`scripts/locate.py` and `references/setup.md` exist in the installed entry. Run the check
for your host:

**Claude Code**

```sh
ls ~/.claude/skills/repo2test/SKILL.md \
   ~/.claude/skills/repo2test/scripts/locate.py \
   ~/.claude/skills/repo2test/references/setup.md
```

**Codex**

```sh
ls ~/.agents/skills/repo2test/SKILL.md \
   ~/.agents/skills/repo2test/scripts/locate.py \
   ~/.agents/skills/repo2test/references/setup.md
```

Open a new agent session. In Claude Code, type `/` and confirm `repo2test` appears in the
command menu. In Codex, type `$` and confirm `repo2test` appears in the skill selector.
The installation is complete when the installer succeeds and the files exist. Host
discovery is the final confirmation and needs a new session: an agent that cannot access
the interactive selector should report the installation as complete, state that host
discovery is unverified, and tell the user how to check it.

**An installation-only task ends here.** If invoking the skill reports a missing test
workspace, the entry has loaded and needs first-use setup. `apitest workspace doctor`
checks a test workspace's runner environment; it does not verify host skill discovery.

## Create your first test workspace

The agent does this on first use. Ask for tests in a repository that has no test workspace
yet, and it checks Python, Git and uv, downloads the latest released runner wheel, verifies
its SHA-256, and shows you the settings it worked out for one confirmation before it creates
anything:

| Setting | What the agent proposes |
|---|---|
| Workspace | `<repository>-e2e` beside the business repository. A workspace anywhere else is found later only when you start the agent in it or name it. |
| Reference commit | The default branch head, marked as an estimate. Name the tag or commit deployed to your test environment to pin that instead. |
| Runner maintainer | Your account on the Git host. |
| CI | GitHub or GitLab, from the business repository's remote. |
| Service URL | The local address your repository's configuration declares, when it declares one. Cases can be written before a URL is set. |

After you confirm, it initializes the workspace, makes it a local Git repository with one
commit and continues with your request. Three things stay with you: saying that an
environment is non-production, supplying credentials and the URLs of remote environments,
and hosting the test repository.

You need:

- Python **3.11+**, Git and [uv](https://docs.astral.sh/uv/), with access to third-party
  Python packages for the runner environment. The agent offers to install uv when it is
  missing.
- An authenticated Claude Code or Codex session and a local business repository checkout.

**In Codex** the default `workspace-write` sandbox blocks network access and restricts
writes. The agent first requests approval to execute a blocked command, then continues
itself after approval. Creation still waits for your confirmation of the settings.
`--add-dir` permits ordinary writes in an additional directory, but protected paths such
as `.git` can remain read-only. On Linux, sandbox placeholder directories can make an empty
destination appear non-empty to `workspace init`, and prevent `git init` (verified with
Codex CLI 0.160.0).

Only when the session cannot approve the required operation does the agent give a manual
step: relaunch with `-c sandbox_workspace_write.network_access=true` for network access,
or run the listed initialization commands in your terminal after confirming the settings.
After those commands succeed, the agent checks the workspace and continues. See
[Codex permissions](https://learn.chatgpt.com/docs/agent-approvals-security) for the sandbox
and approval controls. Claude Code asks for permission as it goes.

**Already have a team test repository?** Clone it beside your business repository so its
relative `[target].path` in `repo2test.toml` (usually `../<repository>`) resolves to your
local business checkout, then skip to [workspace configuration](#configure-the-test-workspace).

### Initialize a new workspace

To do it by hand instead, run this once for each business repository. Examples use
`/work/orders` as the application and `/work/orders-e2e` as an empty destination. Replace
those paths, `v1.4.0` with the ref deployed to your test environment, and `@qa-runners`
with your runner maintainer's actual platform username or team.

Download the runner wheel from the
[latest release](https://github.com/lawli/repo2test/releases/latest) and compare its SHA-256
with the one in the release notes. Replace `0.2.5` with that release's version:

```sh
VERSION=0.2.5
curl -fLO \
  "https://github.com/lawli/repo2test/releases/download/v$VERSION/apitest-$VERSION-py3-none-any.whl"
WHEEL="$PWD/apitest-$VERSION-py3-none-any.whl"

uvx --from "$WHEEL" apitest workspace init /work/orders-e2e \
  --target /work/orders \
  --ref v1.4.0 \
  --runner-wheel "$WHEEL" \
  --runner-owner @qa-runners \
  --ci github
```

This needs no framework checkout.
All options shown are required; use `--ci gitlab` for GitLab. The destination must be
empty or absent and outside the business repository. Initialization creates local files
and resolves `uv.lock`, which needs package-index access; add `--offline` when the uv cache
already holds the packages. If resolution fails, `init` removes what it wrote, so the same
command can be rerun. It never creates a remote: host the test repository alongside the
business repository in the same organization or group through your team's normal Git workflow.

Any other trusted wheel works the same way, such as `vendor/apitest-*.whl` in an existing
team workspace. From a framework checkout you can build one instead:

```sh
uv sync --locked
uv build --wheel
WHEEL="$(ls -t dist/apitest-*-py3-none-any.whl | head -1)"
```

Then run the `init` command above with `uv run --locked apitest` in place of
`uvx --from "$WHEEL" apitest`. A build from a commit other than the release tag has the
release's version but different bytes, and a workspace initialized from it cannot upgrade to
that release; see [Releasing](#releasing).

The installed entry carries these steps in `references/setup.md`. Its
`scripts/fetch_wheel.py` downloads the wheel: it reads only this repository's latest release
and refuses a wheel whose SHA-256 differs from the one the release publishes. Name another
wheel, such as your team's `vendor/apitest-*.whl`, when the agent should use that one.

**For agents handling a setup request:** take each value from the user's request when it is
there, otherwise derive it as below; never invent one. Show all of them in one confirmation
before initializing.

| Option | Source |
|---|---|
| `--target` | The business repository path, inside a Git repository. |
| `--ref` | The tag or commit the user names as deployed to the test environment; it must resolve in the local business checkout. If a named ref is missing there, ask the user to make it available; do not fetch in the business checkout or substitute another ref. If only the deployment time is known, use `git -C <business> rev-list --first-parent -1 --before=<time> <branch>`. If the user named neither, do not ask: use the default branch head and add `--estimated-because "<reason>"`. |
| `--runner-owner` | The maintainer the user names, written as `@user` or `@group/team`; otherwise the current user's account on the Git host, from a platform tool such as `gh`. Ask only when no tool reports one. |
| `--ci` | `github` or `gitlab`, from the host of the business repository's remote. Ask only when it has no remote or the host is neither. |

### Configure the test workspace

For either an existing team workspace or a newly initialized one, run these commands from
the **test workspace** to prepare the environment and configure its first service:

```sh
cd /work/orders-e2e
uv sync --locked

uv run --locked apitest workspace configure \
  --service orders \
  --url https://orders.test.example.com

uv run --locked apitest workspace doctor --sync
```

The URL is an example: supply your actual service URL. The command writes an environment
variable reference into `profiles/local.yaml` and the value into the ignored workspace
`.env`. Repeat it for additional services; add `--profile staging` to configure a named
profile. In an existing team workspace, first check whether the profile already references
the service URL, for example `${LOCAL_ORDERS_URL}`; if so, add only that variable to `.env`,
because `workspace configure` rewrites the committed profile file.

Only `<workspace>/.env` is loaded, and a variable already set in the process environment
takes precedence over it. Selecting a profile does not load `.env.<profile>`. Before
switching targets, check for conflicting exported variables without printing their values.

Add `--environment non-production` only when the user states that the service is a
non-production environment; never infer it from a hostname such as `staging` or `test`.
Until then, read-only cases run and mutating cases stay blocked.
Each profile must declare its own environment; `extends` does not inherit authorization
to write. Cases that call Python helpers run only against non-production profiles; see the
[helper rules](docs/runner-reference.md#python-helpers-and-environments).

The workspace contains:

```text
orders-e2e/
├── repo2test.toml                      # Business repo and reference commit
├── runner.toml                         # Protected runner identity and provenance
├── tests/                              # YAML cases, helpers and fixtures
├── coverage/                           # Per-endpoint evidence and coverage rows
├── defects/                            # Suspected-defect notes
├── profiles/                           # Environment configuration references
├── vendor/                             # Pinned apitest wheel
├── pyproject.toml + uv.lock            # Reproducible dependency environment
├── pytest.ini                          # Runner defaults, including the `local` profile
├── tools/                              # Entry installer and entry sources for either host
├── .claude/skills/repo2test-workspace/ # Pinned authoring rules used by both hosts
├── AGENTS.md, README.md                # Pointers for agents and people
├── .github/ or .gitlab-ci.yml          # Validation job and CODEOWNERS for the chosen CI
└── .gitignore, .gitattributes          # Ignored local files; exact pinned asset bytes
```

Later commands add ignored local files: `.env` (`workspace configure`), `.venv/`
(`uv sync`), `.source/` (the isolated business checkout) and `reports/` (the first run).

## Use with your agent

Start the agent in the business repository and grant it access to the sibling test
workspace. Choose the command for your host:

```sh
cd /work/orders
claude --add-dir /work/orders-e2e
```

```sh
cd /work/orders
codex --add-dir /work/orders-e2e --add-dir "$(uv cache dir)"
```

Codex's default sandbox writes only to the session directory and has no network access.
The second `--add-dir` lets `uv sync --locked` use its package cache; when packages must
be downloaded, also pass `-c sandbox_workspace_write.network_access=true`.

You can also start inside the test workspace; the entry reads `repo2test.toml` to locate
the source repo. The workspace is then the session directory, and the source repo only
needs reading:

```sh
cd /work/orders-e2e
claude --add-dir /work/orders
codex --add-dir "$(uv cache dir)"
```

Enter these prompts **in the agent chat**, rather than your shell:

| Goal | Example prompt |
|---|---|
| Whole repository, Claude Code | `/repo2test Create happy-path and edge-case API tests for this repository. Use the local profile. Do not run the tests.` |
| Whole repository, Codex | `$repo2test Create happy-path and edge-case API tests for this repository. Use the local profile. Do not run the tests.` |
| A specific feature | `Use repo2test to create E2E tests for refunds, including partial refunds, authorization, and repeated requests. Keep the scope to refunds.` |
| Continue previous work | `Use repo2test to continue uncovered refund scenarios. Preserve existing case IDs and manual edits.` |
| Run selected tests | `Use repo2test to run the refund cases against the local profile and report failures, blockers, and cleanup results.` |

Chinese prompts work too: `为这个 repo 创建 test cases` or
`用 repo2test 为退款功能创建 end-to-end test cases`.

When configuration is missing, the agent asks for service URLs while continuing source
analysis and drafts. A generation request produces:

- One coverage fragment per endpoint in scope under `coverage/`; new rows start `uncovered`.
- New cases at `tests/<service>/<scenario>/case_<slug>--<rand6>.yaml`; existing case IDs and
  manual edits are kept.
- One `defects/<slug>.md` note per suspected contract conflict, with the endpoint, source
  commit and contract and implementation evidence, marked as not yet reproduced.
- A report with a delivery status, the `apitest validate` result, missing service URLs, and
  separate counts of authored rows, unauthored rows (`pending` in `apitest coverage`),
  pending expectations (`pending_expectations`), confirmed rows that rest on repository
  tests alone (`test_backed`), blocked rows, registrations `reconcile` lists as uncited,
  rows with status `gap` and suspected defects, each linked to its note.

The delivery status is the first of these that applies:

1. `partial` while an in-scope `uncovered` row can still be authored, including later
   batches of a whole-repository request. The report names the next scope to resume.
2. `blocked` when the remaining work needs a user input or permission: an in-scope
   `blocked` row, a missing service URL, or a validation error that needs user input.
   The report names each missing item.
3. `complete` otherwise: every in-scope row is `authored`, or is a `gap` or `needs-review`
   row with its reason, validation passes and the required service URLs are configured.

A reason explains unfinished work; it never makes a `blocked` or `uncovered` row complete.
Pending expectations do not prevent `complete`: those cases exist but await contract
evidence, so the report lists each with the evidence it needs. A generated case has not
yet been verified against a service.

## Rules for agents

At run time the workspace's pinned skill is authoritative; these rules summarize the
boundaries that matter most.

- MUST treat the business repository as read-only: no checkout, fetch, clean or edits.
  Source is read from the isolated `.source/` checkout.
- MUST NOT call services, log in, run tests or clean up data unless the user asked to run.
- MUST NOT mark a profile `environment: non-production` without the user's statement.
- MUST NOT write secret values into cases or profiles; reference `${VARIABLE}` and keep the
  values in the ignored `.env` or CI secrets. Credential validation is pattern-based and
  does not prove a file is free of sensitive data: keep values the user marks sensitive
  external too. Before committing, confirm that `git ls-files .env` prints nothing and
  review the staged diff.
- MUST NOT edit `vendor/`, `runner.toml`, `tools/` or `.claude/skills/repo2test-workspace/`
  except through a deliberate `apitest workspace upgrade`.
- MUST NOT create remotes or tags, push the default branch, force-push, or change Git host
  settings. With a remote, generation authorizes only the scenario branches and PRs
  described in [Best practices](#best-practices), and only when the remote is writable,
  authentication works and the default branch is known. Commit only files this request
  changed. If publishing fails, keep the local branch and report the missing prerequisite.
- SHOULD derive setup values and confirm them once, ask separately only for an input that
  has no default, such as the URL of a remote environment, and continue source analysis
  meanwhile.

## Run tests without an agent

From the test workspace, validate and check the prerequisites for the selected service:

```sh
uv sync --locked
uv run --locked apitest validate --profile local
uv run --locked apitest preflight --id orders --profile local
```

When ready to call the service:

```sh
uv run --locked apitest run --service orders --profile local --report both
uv run --locked apitest coverage --results reports/summary.json
```

Use `--id orders/refunds` instead of `--service orders` to select a scenario directory.
Selectors must match an existing path under `tests/`; `apitest list` shows the available
cases. `run --dry-run` checks schemas and previews the selection without calling services.

Each executed run writes `reports/summary.json`; the default `--report html` adds
`reports/index.html`, `--report junit` adds `reports/junit.xml`, and `--report both` adds
both. A dry run, or a command that fails before execution, writes no new report, so check
the exit status and the report's modification time before summarizing results.
`apitest coverage` without `--results` shows authoring progress. `validate` and `preflight`
exit 2 when they find problems; `run` exits nonzero when any case fails, errors, is blocked
or unexpectedly passes a known-defect marker.

| Outcome | Meaning |
|---|---|
| `PASS` | Assertions and cleanup succeeded. Only confirmed expectations qualify for verified coverage. |
| `FAIL` / `ERROR` | An assertion, execution step or cleanup failed; inspect the report. |
| `BLOCKED` | Required configuration, fixtures or write prerequisites are missing. Other selected cases continue; the run is incomplete and exits unsuccessfully. |
| `XFAIL` | A business assertion failed at the location of a strict known-defect marker, or a step marked `at: response` got no response. It does not count as verified coverage. |
| `XPASS` | A marked defect unexpectedly passed. The run fails so the marker can be reviewed. |

### Run in CI

The generated CI job (`.github/workflows/validate.yml`, or `validate` in `.gitlab-ci.yml`)
runs only `apitest validate`. It is a static check: it never calls a service or executes
a case.

To execute the suite in a pipeline, add your own job to the test repository:

1. Expose each variable the selected profile references to the job as an environment
   variable, from your CI's secret store. `.env` is ignored by Git, so a CI checkout has
   none and the runner reads the process environment instead. `workspace configure` names
   service URLs `<PROFILE>_<SERVICE>_URL`, for example `STAGING_ORDERS_URL`.
2. Run the suite:

   ```sh
   uv sync --locked
   uv run --locked apitest run --service orders --profile staging --report both
   ```

3. Publish `reports/junit.xml` to your CI's test view and keep `reports/` as an artifact.

The job fails whenever `run` exits nonzero, including for blocked cases. Mutating cases
stay blocked unless the committed profile declares `environment: non-production`.

## Best practices

1. **Start with a bounded feature, then expand.** Include the service, workflow, roles and
   relevant contract in your request. For a full repository, review the endpoint inventory
   before completing scenarios in small batches. Keep gaps visible and resume from them.
   With an existing test-repository remote, the skill delivers each scenario as a new
   branch, pushes it and opens a PR against the default branch (a draft PR when the reference
   is not yet deployed). It never pushes the default branch, force-pushes or creates tags.
   Ask for local-only changes when preferred. Without a remote, it delivers a local diff.
2. **Anchor tests to the deployed version.** Select the deployed tag or commit when preparing
   the workspace. The agent reads its isolated source checkout; uncommitted business edits
   help identify scope. Review affected cases when the reference environment changes.
3. **Review the expected behavior.** Use requirements, API contracts and declarative
   constraints as evidence. Keep uncertain expectations pending. Preserve correct assertions
   when an implementation is defective and use scoped `known_defects` markers to track it.
4. **Separate generation from execution.** Inspect the diff and static validation results
   before requesting a run. Supply a service URL during setup; request DB, Redis or RabbitMQ
   access only when a case needs those checks.
5. **Own and clean up test data.** Run mutations only in a confirmed non-production profile.
   Use per-run identifiers, isolated records and executable teardown. Treat cleanup failures
   as failures and use the report's residual identifiers and recovery instructions.
6. **Keep credentials local and identities separate.** Use ignored `.env` files or CI secrets
   and profile references. Configure service and role credentials independently; use later
   steps for tokens obtained through login. Commit configuration keys, not secret values.
   Profiles fail to load when a recognized credential field holds a literal value, and
   `apitest validate` checks every profile. The check is pattern-based, so review staged
   changes for other sensitive values.
7. **Preserve reviewed work.** Reuse existing case identities and coverage keys, retain manual
   changes, and mark obsolete endpoints for review. Start with serial execution; set
   `parallel_safe: true` only after confirming cases can run concurrently.
8. **Pin and review the test toolchain.** Commit the wheel, lockfile and matching workspace
   skill. Use `uv sync --locked` and `uv run --locked`. Require the generated validation job
   and up-to-date branches or a merge queue/train; require independent runner-maintainer
   approval for wheel and lock changes. A repository administrator enables these
   protections on the Git host; agents do not change host settings.

## Updates and troubleshooting

Update the user entry from your chosen framework checkout with `--update`:

```sh
python3 src/apitest/assets/install_entry.py --host codex --update
```

Use `--host claude` for Claude Code. From a team test workspace, use
`python3 tools/install_entry.py --host codex --update`. Existing workspaces keep their
pinned rules and runner; upgrading those is a separate reviewed change. Always run the
upgrade with the new wheel's CLI, because an older workspace CLI cannot apply newer layout
changes. `$WHEEL` is the new wheel, obtained as in
[Initialize a new workspace](#initialize-a-new-workspace):

```sh
uvx --from "$WHEEL" apitest --workspace /work/orders-e2e workspace upgrade \
  --runner-wheel "$WHEEL"
```

From the new framework checkout, `uv run --locked apitest` is equivalent.

Review the printed compatibility notes; for example, 0.2.3 rejects literal credentials in
profiles. Then run `uv sync --locked`,
`uv run --locked apitest workspace doctor` and `uv run --locked apitest validate` in the
workspace. The upgrade preserves manual case edits. See the
[maintenance guide](src/apitest/assets/skills/repo2test-workspace/references/maintenance.md)
for migrations, reference updates and old-version regression runs.
Changed wheel contents require a new distribution version; reinstalling the identical wheel
can restore its pinned assets without changing the version. If `workspace upgrade` reports
`Changed runner bytes require a new distribution version`, stop and obtain a correctly
versioned bundle from the runner maintainer; never edit `runner.toml` to bypass the check.

| Symptom | Next step |
|---|---|
| The skill is not discovered | Follow the [installation checks](#verify-the-skill-installation) in a new host session. |
| `Entry exists at <path>` | The entry is already installed. Verify it; add `--update` only to replace it deliberately. |
| `Existing entry is a symlink` | The entry path is a link that another installation manages, and the installer does not write through it. `ls -l <path>` shows where it points; update the entry there, or ask the user which installation should own it. Do not remove a link you did not create. |
| `init` reports that the partial workspace was removed | Fix package-index access, or add `--offline` when the uv cache holds the packages, then rerun the same command. |
| `Choose an empty destination` | Select a new or empty directory. Do not delete files you did not create. |
| `git failed: fatal: Needed a single revision` during `init` | The ref is not in the local business checkout. Ask the user to make it available; do not fetch there or substitute another ref. |
| `Changed runner bytes require a new distribution version` | Stop and obtain a correctly versioned bundle from the runner maintainer; do not edit `runner.toml`. |
| `Sibling workspace targets a different business repository` | The `<repo>-e2e` beside this checkout belongs to another business checkout. Give the agent the correct workspace or move the clone. |
| The skill reports a missing workspace | The entry has loaded. Complete [first-use setup](#create-your-first-test-workspace), or place an existing workspace beside the business repo as `<repo>-e2e` or give the agent its location. Check `[target].path` in `repo2test.toml`. |
| Writing the workspace is denied | Relaunch the host with `--add-dir` pointing at the test workspace. |
| Dependency synchronization fails | Run `uv sync --locked` in the workspace and resolve the reported Python, package-access or permission problem. In Codex, grant `--add-dir "$(uv cache dir)"` and, for downloads, `-c sandbox_workspace_write.network_access=true`. |
| Cases are blocked | Run `apitest preflight --id <selector> --profile <profile>` and supply the listed inputs or cleanup prerequisites. |
| `no case matches` or `selector path not found` | Run `apitest list` and select an existing path under `tests/`. |
| `literal credentials in committed profiles` | Move each reported value to `.env` under the suggested name and reference it as `${NAME}`. |
| `required env var not set: NAME` | Add `NAME` to the workspace `.env` or export it in the shell. |
| `workspace doctor` reports changed pinned assets | Do not edit pinned files. Restore them by rerunning `workspace upgrade` with the vendored wheel, or upgrade deliberately. |
| Coverage rejects a previous result | Run the current selection again; verified coverage is tied to the workspace contents and source anchors used for that run. |

## Documentation

- [Runner reference](docs/runner-reference.md): selectors, request syntax, matchers, fixtures, Docker and CLI commands.
- [Authoring guide](src/apitest/assets/skills/repo2test-workspace/references/authoring.md): discovery, evidence, coverage and incremental changes.
- [Environment guide](src/apitest/assets/skills/repo2test-workspace/references/environment.md): service URLs, authentication and dependency configuration.
- [Case review criteria](docs/tightening-criteria.md): what to check before merging generated tests.
- [Execution guide](src/apitest/assets/skills/repo2test-workspace/references/execution.md): execution prerequisites, outcomes, cleanup failures and known defects.

## Contributing

For a bug report, include the host and runner versions, the command or prompt, expected
behavior, and a minimal example with credentials and private business data removed.
Keep behavior changes accompanied by focused tests and relevant documentation updates.

From the framework checkout:

```sh
uv sync --locked --extra dev
uv run --locked pytest -q
uv run --locked ruff check src tests_internal scripts hatch_build.py
uv run --locked mypy src/apitest
uv run --locked python -m apitest.schema.export
```

Backend integration checks use `TEST_MYSQL_DSN`, `TEST_REDIS_URL`, and `TEST_AMQP_URL`;
they are skipped when those variables are unset.

CI runs these checks on Python 3.11 and 3.13 for every push to `main` and every pull
request. The workspace distribution tests run uv with `--offline`; if they fail on a
machine with a cold uv cache, run the cache-warming step from `.github/workflows/ci.yml`
once.

For live agent acceptance checks, see [host verification](docs/host-verification.md).
The probes record installation scope, runner preparation and directory grants so each
result can be attributed to the scenario actually exercised.

### Releasing

`workspace upgrade` refuses a wheel whose version matches the pinned one but whose bytes
differ, so a released version must identify exactly one wheel.

1. Freeze the source, build the release wheel once and record its SHA256.
2. Workspaces initialized from development builds of the same version cannot upgrade to
   the release; initialize them again from the released wheel.
3. After a release, any change to the wheel's contents needs a new version and a
   `CHANGELOG.md` entry, plus an `upgrade-notes.md` section when existing workspaces must act.
4. Before publishing, initialize a workspace with the previous released wheel, upgrade it
   to the new one, and check `workspace doctor` and `validate`.

## License

Licensed under the **Apache License, Version 2.0**. See [LICENSE](LICENSE) for the full text.
