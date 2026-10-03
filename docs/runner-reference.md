# apitest runner reference

[Back to repo2test](../README.md)

This reference covers the runner's selectors, request syntax, assertions, fixtures and
command-line options. For agent installation and workspace setup, start with the
[project README](../README.md). Run workspace commands with `uv run --locked` from
that workspace; the source-checkout and Docker examples below are for runner development.

A YAML-driven API test platform for HTTP services. Cases are declarative YAML
files; the runner stitches together HTTP calls, DB / Redis / RabbitMQ setup +
verification, and Python helper escape hatches.

**Supported stack (this phase):** HTTP/JSON services. Database, cache and broker checks
support **MySQL, Redis, and RabbitMQ**; other databases and brokers are out of scope for
now. The authoring playbook ([agent authoring entry](agent-test-authoring-prompt.md)) is
verified on **Spring Boot** and **FastAPI**.

## Source checkout

```
uv sync --extra dev
cp .env.example .env   # configure only the dependencies your selected cases need
uv run apitest run --service <service> --profile staging
```

Mutating cases require `environment: non-production`, isolated test-owned data and
executable cleanup. See the [environment guide](../src/apitest/assets/skills/repo2test-workspace/references/environment.md).

`.env` is auto-loaded by the CLI and, inside a workspace, by direct `pytest` runs
(gitignored — never commit). Shell `export`s take precedence over `.env`, so per-shell
overrides still work.

## Quick start (Docker)

```
make docker                          # build local image (apitest:dev)
make SERVICE=<service> docker-test   # run service suite, mount tests + reports
```

Hand-rolled equivalent:

```
docker build -t apitest:dev .
docker run --rm \
  -v "$(pwd)/tests:/work/tests:ro" \
  -v "$(pwd)/profiles:/work/profiles:ro" \
  -v "$(pwd)/reports:/work/reports" \
  --env-file .env \
  --network host \
  apitest:dev run --service <service> --profile staging \
    --report both --report-dir /work/reports/<service>
```

## Selecting what to run

Two selector forms; pick one (they're mutually exclusive).

### `--id` — path-prefix selector (recommended)

Expands to the exact set of pytest node IDs under the prefix. Works at any
depth — service, scenario, suite, case.

```
# the full service suite
uv run apitest run --id <service> --profile staging

# one scenario
uv run apitest run --id <service>/<scenario> --profile staging

# one case
uv run apitest run --id <service>/<scenario>/<case_id> --profile staging

# repeatable — multiple scenarios at once
uv run apitest run --id <service>/<scenario_a> --id <service>/<scenario_b> --profile staging
```

### `--service` / `--scenario` / `--suite` / `--case` flag form

```
uv run apitest run --service <service> --scenario <scenario> --profile staging
uv run apitest run --service <service> --scenario <scenario> --case <case> --profile staging
```

### Tag filtering composes with both

```
# run only P0 cases under one scenario
uv run apitest run --id <service>/<scenario> --tag p0 --profile staging

# run all P0 smoke cases anywhere in the service
uv run apitest run --service <service> --tag p0 --tag smoke --profile staging
```

`--tag` is repeatable and AND-combined.

### Parallelism

`--parallel <n|auto>` runs cases across xdist workers. Cases share one serial group
unless they set `parallel_safe: true`, which permits concurrent execution across services.

### Preview + dry-run

```
# what would run (no infra touched)
uv run apitest list --id <service>/<scenario>

# schema-check the selection and list what would run; no HTTP / DB / Redis / MQ calls
uv run apitest run --id <service>/<scenario> --profile staging --dry-run
```

A case that fails to parse or validate does not abort the run: it is collected
as a single failing case and other selected cases continue unless `--fail-fast` is set.
The static `--dry-run` check instead exits with an error if the selection is invalid.

## Authoring a case

The schema is defined in `src/apitest/schema/case_v1.py` (Pydantic v2). Run
`make schema` to emit the JSON Schema artifact for IDE autocomplete.

Only files named `case_*.yaml` are collected as cases (anything under `_shared/`
or `_helpers/` is skipped). Other YAML under `tests/` — Redis seed files in
`fixtures/`, notes — is never treated as a case. `apitest validate` secret-scans
`.yaml` files outside `_shared/` and `_helpers/`; see [validation scope](#validating-fixtures).

Minimum legacy runner case (workspaces additionally require `source_commit` and `covers`
referencing their per-endpoint coverage fragments):

```yaml
schema: v1
name: case_001_ping
service: <service>
steps:
  - name: ping
    request: { method: GET, path: /api/health }
    assert: { status: 200, json: { status: "UP" } }
```

### Workspace fields and execution prerequisites

| Field | Meaning |
|---|---|
| `source_commit` | Source commit the case was authored against. Required by workspace validation and must match its referenced coverage rows. |
| `covers` | List of `{key, variant}` references to coverage rows; `variant` defaults to `default`. Required in a workspace. |
| `expectation` | `confirmed` (default) or `pending`. Pending cases still execute, but a passing result does not count as verified coverage. |
| `requires` | Additional dependencies, especially those used by Python helpers: `services`, `databases`, and `test_data` are lists; `redis` and `rabbitmq` are booleans. Lists default to empty and booleans to false. |
| `mutates` | `true` explicitly marks a write. When omitted, writes are inferred from HTTP methods, fixtures and Python helpers. `false` declares an evidence-backed read-only case: execute-step POSTs (search, login) and helpers that only read. It is invalid with execute PUT/PATCH/DELETE, setup writes, teardown, `data.cleanup` or `data.resources`. Helper cases run only on non-production profiles either way. |
| `data` | Write ownership and recovery metadata: `isolated` defaults to false, `cleanup` to an empty string, and `resources` to an empty list of descriptive labels. This metadata does not execute cleanup. |
| `setup.steps` / `teardown.steps` | HTTP preparation and cleanup steps with the same syntax as `steps`. Step names must be unique across all three phases. |
| `known_defects` | Strict markers for known business assertion failures; see [known defects](#known-defects-and-outcomes). Defaults to an empty list. |

The runner infers dependencies from declared steps and fixtures. Use `requires` for
dependencies hidden inside helpers, for example:

```yaml
requires:
  services: [orders]
  databases: [orders]
  redis: true
  rabbitmq: true
  test_data: [customer_id]
```

Declare only what the case uses. Selected service URLs, identities, fixture files,
infrastructure settings and external test-data values are checked before execution.
Missing prerequisites produce `BLOCKED` before setup or service calls. Other cases
continue unless `--fail-fast` is set; a run with blockers exits unsuccessfully.

Each key used through `requires.test_data`, `${ctx.profile.test_data.<key>}` or
`${ctx.profile.test_data['<key>']}` must
be declared in the workspace's existing `[test_data]` table in `repo2test.toml`:

```toml
[test_data]
keys = ["customer_id"]
```

Supply its value in the selected profile, typically through an environment reference:

```yaml
test_data:
  customer_id: "${TEST_CUSTOMER_ID}"
```

Keep the actual value in the workspace's ignored `.env` or shell environment. Unused
dependencies are not required by case execution or `preflight`.
Static discovery inspects case fields, fixtures and literal Jinja includes. Dynamic
key access, aliases of the test-data container, or dynamic includes require an explicit
`requires.test_data` list containing every key they may use. Template syntax errors block
execution before service calls.

`verify.db.sql` accepts one read-only `SELECT`, including subqueries and supported pure
built-in functions such as `COUNT`, `SUM`, `COALESCE`, `DATE_FORMAT` and `JSON_EXTRACT`.
It rejects writes, CTEs, multiple statements, executable comments, locking reads,
`INTO`, user variables and arbitrary function calls. Use doubled quotes for SQL string
escapes; backslash escapes are rejected because their meaning depends on SQL mode.
`${...}` may supply values inside a SELECT; the rendered SQL is checked again before
submission. Parameterize complex SQL in a Python helper subject to the write guard.
Verification uses a separate connection and a fresh `READ ONLY` transaction for every
poll, followed by rollback. It cannot read uncommitted setup data or setup temporary tables.
The server also enforces read-only transactions for persistent data changes; see the
[MySQL transaction access modes](https://dev.mysql.com/doc/refman/8.4/en/set-transaction.html).

### Credentials in profiles

Profile files are committed, so they reference credentials instead of holding them. A
profile fails to load when any file in its `extends` chain has:

- a URL password, or a URL parameter such as `password`, that is not exactly one `${VAR}`;
- a string, or a list of strings, without `${VAR}` directly under a key whose name,
  lowercased without punctuation, contains `authorization`, `cookie`, `token`, `secret`,
  `password`, `passwd`, `pwd`, `passphrase`, `apikey`, `credential`, `signature` or
  `privatekey`, such as a header or a `test_data` entry.

`apitest validate` reports these for every profile file, selected or not. Each error names
the field and suggests a variable; values are never printed. Suggestions follow
`<PROFILE>_<SERVICE>_DB_PASS`, `<PROFILE>_MQ_PASS`, `<PROFILE>_REDIS_PASS`,
`<PROFILE>_<SERVICE>[_<ROLE>]_<HEADER>` and `<PROFILE>_<TEST_DATA_KEY>`; any name works.
Numbers and booleans are not checked. There is no exemption: move a flagged value to `.env`
even when it is not secret.

```yaml
services:
  orders:
    headers: { Authorization: "Bearer ${STAGING_ORDERS_AUTHORIZATION}" }
databases:
  orders: { dsn: "mysql://app:${STAGING_ORDERS_DB_PASS}@db.stage.internal:3306/orders" }
```

### A write case with API setup and cleanup

Every write requires profile `environment: non-production`, `data.isolated: true`, a
nonempty `data.cleanup` plan, and executable cleanup. Cleanup may use `teardown.steps`,
`teardown.python`, `teardown.db`, or `teardown.redis.delete`; Redis seeds and RabbitMQ
sniffers also have automatic cleanup. Automatic fixture cleanup does not remove unrelated
business resources created by the API.
The selected profile must explicitly declare its environment; `extends` inherits other
settings but never this write authorization. Python helpers default to writes before import,
including verify and teardown helpers; see [Python helpers and environments](#python-helpers-and-environments).

This example assumes an API that accepts a caller-chosen order ID with PUT, returns
`201` on creation, and returns `204` on DELETE even when that ID is already absent.
Adapt paths and assertions to the application's contract. The ID is available before
setup, so cleanup can address the order even if the create response is lost.

```yaml
schema: v1
name: case_read_created_order
service: orders
source_commit: <reference-commit>
covers:
  - key: orders|GET|/api/orders/{id}|happy|read-created-order
expectation: confirmed
mutates: true
data:
  isolated: true
  cleanup: "Delete the order using the case id_short recorded in residual_data if automatic cleanup fails."
  resources: ["order owned by this run's case id_short"]
setup:
  steps:
    - name: create_order
      request:
        method: PUT
        path: /api/orders/${ctx.case.id_short}
        body: { status: PENDING }
      assert: { status: 201 }
steps:
  - name: read_order
    request: { method: GET, path: "/api/orders/${ctx.case.id_short}" }
    assert:
      status: 200
      json: { data.id: "${ctx.case.id_short}", data.status: PENDING }
teardown:
  steps:
    - name: delete_order
      request: { method: DELETE, path: "/api/orders/${ctx.case.id_short}" }
      assert: { status: 204 }
```

Save the case under `tests/orders/read_order/case_read_created_order.yaml`. In a workspace,
add its matching endpoint fragment, for example `coverage/orders/read_order.yaml`:

```yaml
service: orders
method: GET
path: /api/orders/{id}
rows:
  - key: orders|GET|/api/orders/{id}|happy|read-created-order
    source_commit: <reference-commit>
    status: authored
    expectation: confirmed
    evidence: [{file: api/openapi.yaml, line: 42, kind: requirement}]
```

Replace both `<reference-commit>` values with the selected source commit and the example
evidence with the actual contract location. The profile needs `services.orders.base_url`
and `environment: non-production`; use the [README setup commands](../README.md#configure-the-test-workspace).
See the [authoring guide](../src/apitest/assets/skills/repo2test-workspace/references/authoring.md)
for endpoint inventory and evidence rules.

Setup HTTP steps run before SQL/Redis/MQ setup and Python setup helpers. After setup or
business failures, teardown still attempts each cleanup step. A failed cleanup makes the
final outcome `ERROR`, while the report retains the business outcome, cleanup errors and
residual-data details. Terminal output, JUnit, HTML and `summary.json` include the failed
cleanup steps, residual identifiers and recovery instructions. An HTTP cleanup step without
an explicit status assertion uses the HTTP client's status-error check; add an assertion
for the contract's expected status. A teardown step may list the statuses it accepts,
`assert: {status: [204, 404]}`; setup and execute steps take one status. When a cleanup
request cannot be built because a step did not return a value, the cleanup error names
that step and the status it answered.
RabbitMQ close failures report the sniffer queue names and instructions to check their
owning connection; deletion remains unconfirmed until the broker closes that connection.

### Python helpers and environments

Python helpers are arbitrary code, so the runner cannot prove they only read. A case that
calls any setup, verify or teardown helper runs only against a profile that declares
`environment: non-production`; otherwise it is `BLOCKED` before the helper is imported.
There is no exception for production profiles.

On a non-production profile, helper calls count as writes unless the case declares
`mutates: false`. With that declaration the case needs no isolation, cleanup plan or
teardown; the author is responsible for the declaration being true. Without it, supply
`data.isolated`, a concrete `data.cleanup` plan and executable teardown.

`mutates: false` cannot be combined with setup writes (non-GET setup steps or DB, Redis
or RabbitMQ seeding), execute PUT/PATCH/DELETE, teardown actions, `data.cleanup` or
`data.resources`. Put a read-only login or search in the execute steps instead of setup.

Helper modules live in a suite's `_helpers/` or in `_shared/helpers/`; the runner loads
them by file path, so they need no `__init__.py`. In a workspace, `apitest run` disables
pytest's conftest loading and Python module collection and keeps the workspace root off
`sys.path` (also in parallel workers), so no other workspace Python runs.
`apitest validate`, `apitest run` and `workspace doctor` reject `conftest.py` anywhere
under `tests/`, any other Python file outside `_helpers/` or `_shared/`, which would
never run, and symlinked directories, whose cases would run unvalidated.

### Known defects and outcomes

Keep the correct business assertion and add a marker only for an established defect.
For a known incorrect status value in the example's read response, the case-level field is:

```yaml
known_defects:
  - ref: defects/order-status.md
    step: read_order
    at: json.data.status
    env: local
```

`ref` accepts a tracker ID (`PROJ-123`, `#123`, or `owner/repo#123`), an HTTP(S) issue URL,
or an existing Markdown record under `defects/` inside the workspace. `step` names
an execute step, not a setup or teardown step. Optional `at` selects an existing
assertion: `status`, `json.<path>`, `headers.<lowercase-name>`, `text`, or
`extract.<name>` for a value the step extracts; omitting it
matches any business assertion failure in that step. `at: response` is the one location
that is not an assertion: it matches the step when the service took the request and ended
the connection without a response (`httpx.RemoteProtocolError` or `httpx.ReadError`), and
it must be written out, because a marker without `at` covers assertions only. Optional
`env` selects a profile name; omitting it applies the marker to every profile.

A matching business assertion failure becomes `XFAIL`, and so does a dropped connection on
a step marked `at: response`. If a case with an applicable marker passes, it becomes
`XPASS` and fails the run. Other assertion locations, other transport errors (an
unreachable service, a timeout), blockers and cleanup failures are not suppressed. Neither `XFAIL` nor a passing
case with `expectation: pending` counts as verified coverage. A failing case with
`expectation: pending` is reported as `FAIL` with the prefix `pending expectation not met`
and counted in `totals.pending_fail` of `summary.json`; it still fails the run.

### Requests

```yaml
request:
  method: POST            # GET POST PUT PATCH DELETE HEAD OPTIONS
  path: /api/orders/${ctx.order_id}
  params: { page: 1, owner: "${ctx.case.user_id}" }   # query string
  headers: { X-Trace: "${ctx.case.id_short}" }
  body: { amount: 10 }    # JSON — or one of:
  # body_template: bodies/order.json.j2   (+ body_vars)
  # form: { user: u, pw: p }             # application/x-www-form-urlencoded
  # raw: "<xml/>"                        # templated text; set Content-Type
  # files: { file: fixtures/up.csv }     # multipart (may combine with form)
```

Case-level `headers:` are sent to the default service and role (step headers override; names
are case-insensitive) and are rendered per step, so a token returned by a
`setup.python` helper works: `headers: { Authorization: "Bearer ${ctx.token}" }`.
They must resolve at every step — if the login is itself step 1, put the
`Authorization` header on the later steps instead. Static per-service headers
(API keys) live in the profile: `services.<svc>.headers`.
Use `services.<svc>.roles.<role>.headers` for named identities and select them on each step.
Use `service_headers.<svc>` for case-local per-service headers. A named role never inherits
the default identity's credentials.

Template rendering supports `${…}` and Jinja delimiters (`{{ }}`, `{% %}`, `{# #}`)
in request paths, case/step header values, `raw`, string values in `params`, `body`
and `form`, and JSON/header/text assertion values. These are rendered per step;
mapping keys are not rendered.

`body_template` and `files` contain literal paths relative to the case file's directory.
For example, `files: {file: "${ctx.filename}"}` looks for a file literally named
`${ctx.filename}`. The contents of a `body_template` file are rendered and parsed as
JSON; `body_vars` supplies extra template variables without rendering their values first.
Step names, service/role selectors and extraction expressions are also literal.

### Assertions

```yaml
assert:
  status: 201
  json:
    data.id: "@type number"
    data.items: "@len >= 1"
    data.status: '@in ["PAID", "PENDING"]'
    data.note: "@absent"
  headers: { content-type: "@contains json" }   # names are case-insensitive
  text: "@regex /created/"                       # raw body, for non-JSON responses
```

`assert.json` keys are dotted paths (`data.items.0.id`; list indexes are numbers). The
JSONPath spelling used by `extract` also works: `$.data.id` is `data.id`, and `$` is the
whole body, so `json: { $: 50 }` asserts a scalar response of `50`.

| Matcher | Meaning |
|---|---|
| exact value | `==` after rendering (`"${ctx.order_id}"` keeps its type) |
| `@any` | present, any value |
| `@absent` | JSON path is missing; supported only in `assert.json` |
| `@null` | Present value is null |
| `@type T` | JSON names `string number integer object array boolean null` or Python names |
| `@number > 0` (`>= <= == != <`) | numeric comparison |
| `@len N` / `@len > N` | length of a string, array or object |
| `@contains X` | substring, list member (JSON literal or text) or object key |
| `@in [..]` | membership in a JSON list |
| `@regex /re/` | `re.search` on the string form |
| `@uuid` / `@iso8601` | format checks |
| `@@text` | the literal value `@text`; use it for expected values that begin with `@` |

Response headers named in `assert.headers` must exist. `@absent` cannot assert that a
header is missing and has no absence semantics in `assert.text`.

### Values keep their type

A string that is exactly one `${expr}` renders to the expression's native
value: `body: { id: "${ctx.order_id}" }` sends a number if the extract was a
number, and `assert.json: { data.id: "${ctx.order_id}" }` compares as a number.
Mixed text (`"order-${ctx.order_id}"`) renders to a string. DB row expectations
(`verify.db.expect_rows`) fall back to string comparison against driver types
(Decimal, datetime), and Redis values compare as strings.

### Waiting for asynchronous writes

Any `verify.db` / `verify.redis` / `verify.rabbitmq` entry accepts
`wait: { timeout_s: <n>, interval_s: <n> }` (interval defaults to 0.5s). The
check is retried until it passes or the timeout elapses — use it for writes
that land after the HTTP response (`@Async`, AFTER_COMMIT listeners, outbox
relays). Without `wait`, a verify entry is checked exactly once.

```yaml
verify:
  rabbitmq:
    - sniffer: order_created
      count: 1                     # exact; or count_min / count_max for ranges
      match: { body.orderId: "${ctx.order_id}" }
      wait: { timeout_s: 10, interval_s: 0.5 }
```

`count`, `count_min` and `count_max` all apply to the `match:`-filtered set.

### Redis keys the service reads or writes

Test-written keys are namespaced under `t:<case_id_short>:` by default, which
the service under test cannot see. To seed state the service reads, mark the
entry `unprefixed: true` in the seed file; the key is still tracked and deleted
at teardown. Keys the service itself writes are cleaned via
`teardown.redis.delete` (rendered, deleted verbatim):

```yaml
# fixtures/redis/seed.yaml
set:
  - { key: "session:${ctx.case.id_short}", value: tok, ttl: 60, unprefixed: true }
```

```yaml
teardown:
  redis:
    delete: ["order:cache:${ctx.order_id}"]
```

### HTTP diagnostics and retries

Failure output in pytest, HTML and JUnit includes recorded HTTP calls that returned a
response: method, URL, final status, request/response body previews and retry summaries.
Each body preview is truncated after 2,000 characters. Intermediate retry responses
are summarized, not recorded as separate complete exchanges. If a call ultimately raises
a transport exception, that call has no exchange-log entry; the case reports the error
and any previously recorded calls. `XFAIL` results carry the same exchange log in the HTML
and JUnit reports. `summary.json` stores structured outcomes and errors,
not the HTTP exchange log. Env-sourced secrets resolved through profiles and recognized
credentials are redacted from the recorded exchanges and error messages.

Retries (`http.retries` in the profile) apply to GET/PUT/DELETE/HEAD/OPTIONS on
transient errors and 502/503/504. POST and PATCH are retried only when the
connection never opened; read/write timeouts for those methods are not retried. A DELETE
whose first attempt succeeded but timed out on read is retried and may then
return 404 — pin `status` with that in mind.

### Validating fixtures

`apitest validate` schema-checks collected `case_*.yaml` files and secret-scans `.yaml`
files under the selected directory, excluding any path under `_shared/` or `_helpers/`.
This scan does not cover those excluded fixtures or files with a `.yml` extension.
SQL linting covers every `.sql` file under the directory, including helper directories:

| Rule | Severity | Trigger |
|---|---|---|
| `blanket-delete` | error | `DELETE FROM <table>;` with no `WHERE` |
| `env-var-not-allowed` | error | `${UPPER_CASE}` env reference inside SQL |
| `literal-id` | warning | a 2+ digit integer literal in `INSERT … VALUES` (use `${ctx.case.<id>}`); append `-- allow-literal-id` to the line for amounts / enums |

Inside a workspace, validation also checks coverage references, source anchors, helper
signatures, fixture paths, matcher syntax and stray test-tree Python across the workspace, even when a narrower
directory is supplied. `--base <ref>` adds changed-anchor checks. Static validation does
not replace `preflight`, which checks the selected profile's execution prerequisites.

## CLI

| Command | Purpose |
|---------|---------|
| `apitest workspace init <dir> --target <repo> --ref <ref> --runner-wheel <wheel> --runner-owner <@owner> --ci github` | Generate a self-contained local test workspace; `--ref` is required, and `--estimated-because <reason>` records a deployment estimate |
| `apitest workspace configure --service <s> --url <url> --environment non-production` | Store a service URL in workspace-local ignored configuration |
| `apitest workspace doctor [--sync]` | Check wheel hash, byte-for-byte pinned skill/entry assets, lock and writable workspace |
| `apitest workspace source [--ref <ref>]` | Create an isolated read-only source checkout |
| `apitest workspace reference --ref <ref> [--estimated-because <evidence>]` | Change reference metadata, preserving case anchors |
| `apitest workspace upgrade --runner-wheel <wheel> [--migrate]` | Stage matching wheel, skills, entry sources and lock |
| `apitest preflight --profile <p> [--id <selector>]` | Check necessary configuration without service calls |
| `apitest reconcile --source <checkout> [--pattern <regex> --include <glob>]` | Find route registrations missing from the inventory. Spring mappings and FastAPI route decorators are built in; `--pattern` with `--include` (both repeatable) scans another framework's syntax. `cross_check` names the scans that matched, or `unavailable`. Exits 2 when a registration is missing from the inventory or, with `--pattern`, when cited route evidence was not matched (`pattern.unmatched_evidence`) |
| `apitest coverage [--results reports/summary.json]` | Generate authored/pending/verified coverage view |
| `apitest defects` | Generate strict known-defect view |
| `apitest migrate [tests] [--check]` | Validate current v1 cases without rewriting comments, formatting or source anchors; no schema transition is currently needed |
| `apitest run --service <s> [--profile <p>] [--parallel auto]` | Execute cases; writes `summary.json` (+ HTML / JUnit) under `--report-dir` |
| `apitest list [--id <sel>] [--service <s>]` | List cases that would run |
| `apitest validate [path] [--profile <p>] [--base <ref>]` | Static case/coverage/helper/fixture/matcher validation, literal credentials in every profile file, and changed-anchor checks. Where the source checkout of a row's commit is present, every evidence citation must name a file inside it and a non-blank line of that file |
| `apitest profile show <name>` | Show resolved service, infrastructure and HTTP settings (redacted); omits `environment` and `test_data` |
| `apitest connections show [--profile <p>] [--parallel n]` | Heuristic connection estimate (per case × workers); extra HTTP service/role identities are not counted |
| `apitest sweep --manifest <jsonl> [--profile <p>]` | Replay cleanup manifest |

## Self-tests (regressions to keep green)

| | Guarantee | Test |
|---|---|---|
| D2 | `count` filters by `match:`, not raw queue depth | `unit/test_lifecycle.py::test_d2_sniffer_count_filters_by_match_not_raw_queue_depth` |
| D3 | SETUP SQL failure rolls back, no partial commit | `integration/test_db.py::test_phase_rolls_back_on_failure` (needs `TEST_MYSQL_DSN`) |
| D4 | Sniffer bound in SETUP captures messages emitted during EXECUTE | `unit/test_lifecycle.py::test_d4_sniffer_captures_messages_emitted_during_execute` |
| D6 | Redis teardown deletes tracked keys, never `SCAN MATCH` | `unit/test_lifecycle.py::test_d6_redis_cleanup_deletes_tracked_keys_without_scan` |

## Adding a service

Service names are defined entirely by your profiles — there is no hardcoded
registry. To add one:

1. Add `services:` (and any `databases:`) entries under each profile in
   `profiles/`. The keys you add become the valid service names. Every
   `databases:` key must match a declared `services:` key. A service entry
   may carry default-identity `headers:` (e.g. an API key from an env var).
   Steps using a named role use that role's headers instead.
2. Create `tests/<service>/` and start writing cases.

No platform code changes expected. To statically catch a typo'd service name in
a case, run `apitest validate tests --profile <name>`.

## Development

```
make install        # editable install with dev deps
make test           # run pytest (internal regressions only)
make lint           # ruff
make typecheck      # mypy strict
make schema         # regenerate src/apitest/schemas/case.schema.v1.json
make SERVICE=<svc> staging-run  # run one service against staging
make docker         # build local image
make docker-test    # SERVICE=<svc> runs the image against staging
```
