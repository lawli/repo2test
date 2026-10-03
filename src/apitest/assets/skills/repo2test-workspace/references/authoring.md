# Source, inventory and authoring

Use the reference ref from `repo2test.toml`. Ask for the deployed tag/branch when missing;
if only deployment time is known, resolve the documented first-parent cutoff. If neither
is known, explicitly record that the default-branch ref is an estimate. All services in
this target must share one reference ref in v1. Keep not-yet-deployed behavior in a draft.

Use `apitest workspace source` to create the pinned commit's isolated checkout in
`.source/<commit>/`; it prints its path as `checkout`.
Only select `--ref <ref>` when deliberately inspecting a different deployed reference;
resolve `reference_update_required` before authoring, rather than silently following a moving branch.
Analyze this checkout; record its HEAD as `source_commit`. Never checkout, fetch or clean
inside the business working tree. Identify the web framework from build dependencies and
the application entry. Spring Boot and FastAPI are the verified stacks; for another
framework work the same way and state in the report that the stack is unverified. Report a repository that
serves no HTTP API as out of scope.

For whole-repository requests, first walk every place the framework registers routes:
controllers or routers, composed or decorator mappings, functional routes, mount and config
prefixes, and enabled conditions. Include internal and management routes.
Write **all** endpoint fragments under `coverage/` before creating cases. Every identified
behavior starts `uncovered`; a registration you cannot enumerate is a discovery gap, one
row with `status: gap` and a reason. Run
`apitest reconcile --source <checkout>`. It finds route registrations by syntax and lists
each one the inventory does not cite as `kind: route` evidence, on the endpoint or on a row,
and exits 2 while it lists one: record each missing mapping in a fragment, or explain it in
the report with its file and line; the mechanical scan is not a runtime route inventory. Spring mapping annotations with RouterFunction, and FastAPI
route decorators, are built in. Use class-level mapping, mount and prefix evidence
alongside method evidence. When it reports
`"cross_check": "unavailable"`, nothing matched and an empty gap list proves nothing: the
inventory rests on your reading alone, so cite the registration line of every route and
any API document the repository checks in or generates.

On a framework without a built-in scan, pass its registration syntax yourself, after the
fragments exist: `apitest reconcile --source <checkout> --pattern '<regex>' --include
'<glob>'`, both repeatable.

- Derive the patterns from the registration lines you already cited as route evidence, one
  `--pattern` per registration form. A pattern must match the first line of every
  registration of its form: a match counts as recorded only when route evidence cites the
  same file and line. The point is to find registrations of that form your reading missed.
- The pattern is a Python regular expression in multiline mode, so `^` anchors a line. An
  Express repository with direct and chained registrations needs two, for example
  `--pattern '\b(?:app|router)\.(?:get|post|put|patch|delete|options|head|all)\('` and
  `--pattern '^\s*\.(?:get|post|put|patch|delete|options|head|all)\('`, with
  `--include 'src/**/*.js'`, and more if you cite mounts. This shows the syntax; take the
  patterns from the source in front of you.
- `'*.ts'` matches at any depth; a glob with a directory, such as `'src/**/*.go'`, is
  relative to the checkout, and `**` also matches no directory, so it reaches files directly
  in `src/`. Dependency directories are skipped. Tests are left out only by choosing globs
  that do not reach them.
- Read the `pattern` block before the gap list: `files` 0 means the glob matched nothing
  and `mappings` 0 means the pattern matched nothing. `unmatched_evidence` lists route
  evidence you cited that no pattern matched: the patterns or the globs miss that
  registration form, so an empty gap list proves nothing yet. Add a pattern or widen the
  glob until it is empty; the command exits 2 while it is not.
- A pattern describes a registration form, so do not narrow it to names only your cited
  lines contain. It can then match a line of the same form that is not a route, such as a
  rate limiter mounted on a path. Leave that line in the gap list and explain it in the
  report; do not cite it as route evidence to empty the list. A run that exits 2 with every
  remaining line explained is a finished cross-check.
- Routes registered by convention, by file location or at runtime have no line to match.
  Keep them as rows you verified by reading, or as discovery gaps.
- State the patterns, the globs, both counts and every explained line in the report.

For feature requests, select the endpoints implementing that feature before writing
coverage fragments. Shared controllers and repository-wide `reconcile` output may expose
other endpoints; use them as context without expanding the selected inventory. Treat only
missing in-scope mappings as discovery gaps for this request. Inventory all selected
endpoints and behaviors before drafting cases, and report coverage for that feature.

An endpoint fragment is YAML:

```yaml
service: orders
method: GET
path: /orders/{id}
conditions: []
evidence: [{file: src/main/java/Orders.java, line: 12, kind: route}]
rows:
  - key: orders|GET|/orders/{id}|happy|existing-order
    source_commit: <reference-commit>
    status: uncovered
    expectation: confirmed
    evidence: [{file: api.yaml, line: 40, kind: requirement}]
```

Evidence `kind` is `route`, `requirement`, `declarative`, `test` or `implementation`; the
last cites the handler code that shows current behavior and never confirms an expectation.
Row
`status` is `uncovered`, `authored`, `blocked`, `needs-review` or `gap`; `expectation` is
`confirmed` or `pending`; any row may carry a `reason`. `conditions` is a list of strings.
`service` is the name the profile and the cases use; when no profile exists yet, choose it
now and keep it. Fragments are the `*.yaml` files anywhere under `coverage/`, by convention
`coverage/<service>/<METHOD>_<path>.yaml`.

Coverage keys contain service, uppercase method, normalized path, category and stable
behavior anchor. Categories: happy, boundary, validation, auth, state, idempotency,
pagination, dependency, async, error. `variant` distinguishes deliberately different
cases for one key. One endpoint per fragment; extend existing fragments.
The normalized path is the route as a case requests it under the service `base_url`:
prefixes applied, parameters as `{name}`, a declared trailing slash kept. Evidence `file`
is relative to the checkout root; `line` is the first line of the declaration it cites.
When one statement registers several endpoints, cite for each endpoint the line that
names its method, and the line that carries its path when that is another line. Name path
parameters as the registration does. Where a mount and a route of `/` meet, write the path
without the trailing slash unless the framework serves the two forms differently.

Trace handler → service → query/persistence and external calls. Build happy, boundary,
validation, role/permission and state cases from evidence. Requirements and API contracts
define expectations. Declarative constraints are contract evidence (`kind: declarative`,
with file/line): Bean Validation, typed request and response models with their field
constraints, the status code and response model a route declares, enum/state definitions
and OpenAPI annotations. A test checked into the business repository that asserts the
behavior is contract evidence too (`kind: test`, citing the assertion line), the weakest
kind: use it only where no requirement or declarative constraint settles the behavior,
and never to overrule one. Imperative branches
identify scenarios but do not establish correct expected behavior. Insufficient evidence
means `expectation: pending`, not invented error codes or weakened assertions.
When the implementation contradicts the contract, keep asserting the contract and record
each conflict in its own `defects/<slug>.md` note: the endpoint, source commit, and the
contract and implementation evidence with file/line. Mark it as not yet reproduced and link
the note from the final report. Add `known_defects` only after an execution reproduces it.

Create `tests/<service>/<scenario>/case_<slug>--<rand6>.yaml` once. Keep that identity on
updates. The installed schema (`apitest.schema.case_v1.Case`) is authoritative. Cases use
`schema: v1`, `name`, `service`, `source_commit`, `covers: [{key, variant}]`, and `steps`.
Each step has `name`, optional `service`/`role`, `request`, `assert`, and `extract`.
Extracted values are available as `${ctx.<name>}` in later steps. `assert.json` keys are
dotted paths such as `data.id`; `$.data.id` and `$` (the whole body) also work. List
indexes in a path are numbers (`data.items.0.id`). `${...}` holds a Jinja expression and
renders in request paths, header values, `params`, `body`, `form`, `raw` and assertion
values. Step names, `extract` expressions and helper `args` are literal, so a helper
takes `ctx` and reads the values it needs from it.

An expected value is compared exactly after rendering, unless it is a matcher: `@any`
(present), `@absent` (path missing; `assert.json` only), `@null`, `@type T` (`string`,
`number`, `integer`, `object`, `array`, `boolean`, `null`), `@number > 0` (also `>=`,
`<=`, `==`, `!=`, `<`), `@len N` or `@len > N`, `@contains X` (substring, list member or
object key), `@in [..]` (a JSON list), `@regex /re/`, `@uuid`, `@iso8601`, and `@@text`
for a literal that begins with `@`. `assert.headers` (names are case-insensitive, and a
named header must exist) and `assert.text` (the raw body) take the same matchers.

Use `setup.steps` and `teardown.steps` for API preparation and cleanup. A teardown step
may list the statuses it accepts, `assert: {status: [204, 404]}`, when an earlier delete
or a cascade may already have removed the record; setup and execute steps assert one
status. Writing cases
declare `data: {isolated: true, cleanup: <concrete recovery instructions>, resources: [...]}`
and executable teardown. Use `${ctx.case.id_short}` for per-run ownership. `mutates: false`
is reserved for evidence-backed read-only cases: execute-phase POST/search/login and
read-only helpers. It cannot be combined with setup writes, execute PUT/PATCH/DELETE,
teardown, a cleanup plan or `data.resources`, so put a read-only login in the execute steps. Method choice alone does
not prove a business operation is harmless. Missing isolation or cleanup blocks execution.

Use DB/Redis/MQ verification only when necessary business effects cannot be observed by
API. Python helpers declare hidden dependencies through `requires` (services, databases,
redis, rabbitmq, test_data). Declare test-data keys in `repo2test.toml`; values are external.
Use dotted or constant-index access for discoverable test-data references; declare every
key in `requires.test_data` when using dynamic access or dynamic template includes.
Keep `verify.db.sql` to a single read-only SELECT using supported built-ins. Verification
has its own read-only connection and cannot see setup temporary tables or uncommitted data.
Use guarded Python helpers for SQL outside that subset.
Cases that call Python helpers, including verify helpers, run only on profiles marked
`environment: non-production`; production profiles block them before import. Declare
`mutates: false` only when every helper in the case really just reads; the case then
needs no isolation or cleanup. Otherwise helper calls count as writes and need isolation
and executable cleanup. Prefer declarative read steps and assertions over helpers.
Put helper modules in the suite's `_helpers/` or in `_shared/helpers/`. Never add
`conftest.py`, `__init__.py` or other Python elsewhere under `tests/`, and never symlink
test directories; validation rejects both.

On repeat requests, read fragments and cases, reuse existing coverage, and draft missing
rows within the selected scope first. Leave existing unrelated fragments and cases
unchanged. Preserve human edits; show evidence and differences for needed updates and
ask only where changes conflict. Mark removed/stale endpoints `needs-review` instead of
deleting cases. Every `gap`, `blocked` or `needs-review` row states its `reason`. Report uncovered, blocked and needs-review rows even during partial runs.
