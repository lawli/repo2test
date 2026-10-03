# Live host verification

Run these probes from the framework checkout after `uv sync --locked --extra dev` and
`uv build --wheel`. They require an authenticated CLI for the selected host and cached
runner dependencies. They use disposable Spring source and test workspaces, plus a local
HTTP listener that records every HTTP method, including login POSTs and unsupported verbs.
Creation prompts contain no extra prohibition on service calls: the installed skill must
enforce its own creation-only boundary. Business snapshots compare file contents, HEAD,
the index and status, including files that were already dirty before the host started.

`verify_host_authoring.py` defaults to the real user-level skill directory and a cold
runner: the test workspace has no `.venv` before the host starts. An existing user entry
must match the supplied wheel; the probe refuses to overwrite a different installation.
If the entry is absent, it installs it temporarily and removes that copy when the probe
finishes. Artifacts remain under `~/.cache/repo2test-host-probes/`.

```sh
# Whole-repository request with no skill name in the prompt; cold user-level install.
uv run --locked python scripts/verify_host_authoring.py codex \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --mode natural --scope whole --timeout 600
uv run --locked python scripts/verify_host_authoring.py claude \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --mode natural --scope whole --timeout 900

# A single feature: the inventory must stay within the refund quote (S4, S16).
uv run --locked python scripts/verify_host_authoring.py claude \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --mode natural --scope feature --timeout 900

# Conditional and management routes plus a contract/implementation conflict (S1, S12).
uv run --locked python scripts/verify_host_authoring.py claude \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --mode natural --extended-fixture --timeout 900

# The host session starts inside the test workspace (S18).
uv run --locked python scripts/verify_host_authoring.py claude \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --mode natural --start-in workspace --timeout 900

# Codex must report the missing sibling-directory permission, without generating cases.
uv run --locked python scripts/verify_host_authoring.py codex \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --scenario sandbox --timeout 240

# Ask for a missing URL without inventing one or calling a service.
uv run --locked python scripts/verify_host_authoring.py claude \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --scenario missing-url --timeout 900

# Resume persisted coverage after interruption; keep the existing manual case byte-for-byte.
uv run --locked python scripts/verify_host_authoring.py codex \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --scenario resume --stale-endpoint --timeout 900

# Offline empty dependency cache: report sync failure and recovery without authoring.
uv run --locked python scripts/verify_host_authoring.py claude \
  --wheel dist/apitest-0.2.3-py3-none-any.whl --scenario sync-failure --timeout 300
```

The positive scenarios grant the sibling workspace and uv cache as additional directories.
A workspace start (`--start-in workspace`) runs the host inside the workspace and grants
the uv cache; Claude Code also receives the business repository, which it may only read.
That business repository is named `refund-service`, so the `<target>-e2e` naming
convention does not lead to it, and the prompt names neither repository. The Codex sandbox
scenario grants neither and keeps its sibling outside `/tmp`, which is normally writable
in that sandbox. It succeeds only if the host reports `--add-dir`, leaves the workspace
unchanged and does not synchronize the runner or call the service.

Every authoring probe also checks order: a `workspace doctor` invocation whose output
reports no problems, and for a workspace start the entry's `locate.py --start`, must come
before the first change to `tests/` or `coverage/`. The probe timestamps each host output
line as it arrives (`host-events.jsonl`) and polls those directories while the host runs,
recording when a new, changed or removed entry was first seen (`tree_changed_at` in
`probe.json`); a later rewrite of the same file cannot hide that first change. The check
compares the doctor result and locator call with the earlier of that time and the start of
any command that looks like a write. Artifacts recorded before timestamps, or before
polling (`f0508c7`), rely more on the transcript. Without timestamps the check finds the
first write in the transcript: editor tools, patches, redirects, copies, Python file I/O,
a `cd` into those directories and any script other than the locator count as writes. A
write that no command reveals can still escape that fallback, so `order_evidence` in
`probe.json` and each re-evaluation records which method was used.

After an oracle change, re-check retained artifacts without rerunning the host. Each run
writes `reevaluation-<commit>.json` beside the untouched `probe.json`:

```sh
uv run --locked python scripts/verify_host_authoring.py reevaluate \
  ~/.cache/repo2test-host-probes/<artifact-directory>
```

Use `--scope feature --mode natural` for the feature request that explicitly names
repo2test. `--installation project` and `--warm` are separate diagnostic variants; their
results do not establish user-level discovery or cold-start behavior.

Inspect `probe.json`, `host.log`, `final.log` and `validate.log`. `observations.json` retains
the host exit code and recorded calls even if a later evaluator fails.
A successful authoring probe requires
the expected endpoint inventory, passing static validation, a runner environment created
by the host, a final user-facing response, no service calls and an unchanged business working tree. A fixture-specific
oracle checks health plus refund interior/minimum/maximum, immediately out-of-range values,
missing parameters and non-integer input. It checks that confirmed assertions accept the
contract response and reject wrong status/body values, entirely in memory without running
the generated cases or helpers. Merely producing case files cannot satisfy this oracle.
With `--extended-fixture`, the probe also requires the conditional and management routes
in the inventory (the actuator discovery page `/actuator` is allowed, not required), an
enabling condition or reason recorded for `/api/promo`, a case asserting the contract's
404 for refund id 0 and no confirmed case accepting the implementation's 200, and a
`defects/*.md` note about the refund lookup that cites both 404 and 200. A conflict stated
only in the final report is not parsed for a verdict: the probe fails and asks for manual
confirmation. Interrupting a probe with Ctrl-C, SIGTERM or a closed terminal stops the
host's process group and still removes the temporary entry.
The resume scenario additionally requires preservation of the existing case. A successful
missing-URL probe checks that the final response mentions the URL and the selected service
still has no configured URL. Unresolved URL placeholders are allowed; concrete values in
the profile, inherited profile or workspace `.env` fail the check. This does not claim
generation is complete. Review the final response for actionable configuration guidance;
model output and behavior beyond this small fixture still require review.

`--stale-endpoint` adds a real previous source commit containing `/api/retired`, removes
that route in the selected deployment reference, and seeds its manually maintained case
and old coverage row. The resume probe requires the case bytes to survive and the row to
be marked `needs-review`. `sync-failure` gives the host a writable empty uv cache with
`UV_OFFLINE=1`; it requires actionable `uv sync` guidance and no generated cases or calls.

`verify_host_entry.py` is a smaller **project-scoped sibling-access probe**. Its results
cover only that access check, not user-level installation, dependency synchronization or
natural-language authoring. Every live result applies to the recorded scenario and host
version; unrun scenarios must remain explicitly unverified.
Claude logs include tool events via stream JSON; `--max-turns` defaults to 80. A timeout
or turn-limit exit is a failed probe even if partial artifacts pass static validation.
Claude uses its default session persistence. Disabling it broke the installed `claude-mem`
Stop hook in this environment, causing repeated transcript errors and an empty final
result despite valid cases. Probes retain normal host settings and plugin behavior.

## Historical 0.2.0 runs

These runs precede the 0.2.1 audit fixes. Their runtime source SHA256 is
`a009a82d456b3478ffb3dee949c03c19f67fba8b4d1d0c72984116a0884c9c0a`.
The earlier prompts explicitly prohibited requests and the listener recorded only GET;
they do not establish the default creation behavior checked by the revised probe.

On 2026-09-28, the following runs used Codex CLI 0.157.1 and Claude Code 2.1.283.
Every listed run began with no workspace `.venv` and used the real user-level entry.
The source fixture has two endpoints; these checks cover authoring and static validation,
not execution against a deployed Spring application.

| Scenario | Host | Observed result |
| --- | --- | --- |
| Whole-repository natural request without a skill name | Codex | Passed: user entry loaded, runner synchronized, 25 valid cases covering both endpoints |
| Whole-repository natural request without a skill name | Claude Code | Passed: user entry invoked, runner synchronized, 12 valid cases covering both endpoints; 71 turns |
| Sibling workspace denied by sandbox, no extra directory grants | Codex | Passed: locator reported unwritable workspace, final response provided `--add-dir`, no workspace changes or runner sync |
| Resume persisted partial coverage | Codex | Passed: 15 valid cases covering both endpoints; original case and manual comment preserved byte-for-byte |
| Missing service URL | Codex | Passed: 18 valid cases, final response requested the deployed base URL, configuration remained incomplete, no concrete URL invented |

All of these runs left the business working tree unchanged and recorded no requests to
the fixture service. Resume starts from a seeded interruption checkpoint with one authored
case and one uncovered endpoint; it does not kill a host process during a filesystem write.

The first Claude natural-request attempt reached a 45-turn cap and failed, despite
producing 12 statically valid cases. The successful rerun used the current 80-turn cap.
An initial project-scoped missing-URL run also exposed a probe evaluator bug: unresolved
URL placeholders made `profile show` fail. The evaluator now resolves only the service
URL and distinguishes missing values from concrete values without requiring other secrets.
The subsequent user-level missing-URL run passed; its final response explicitly left
delivery pending URL configuration. Final-response guidance was inspected in the host logs
for the recorded runs; newer probes also save a separate `final.log` and check that output
rather than matching text from tool events or the original prompt.

Feature-scope authoring and the negative scenarios on Claude Code were not rerun for this
revision. These results do not establish full design acceptance or correctness of every
generated assertion.

## 0.2.1 verification

The 0.2.1 wheel was built with runtime source SHA256
`bf31ee03704053fdd4a2c395f30b54929851a95d5a824f611c63d5a2382fd4dc`.
Its wheel SHA256 is `e424677b5640f1a5735068642022a499c9857304b063eba0d42ec069b2382204`.
These runs use natural whole-repository creation prompts without a skill name or an
extra instruction forbidding requests. Both start with a user-level entry and cold runner.

| Host | Artifact directory under `~/.cache/repo2test-host-probes/` | Result |
| --- | --- | --- |
| Claude Code 2.1.283 | `claude-natural-authoring-bwqttex7` | Artifact checks passed: 12 cases, both endpoints, static validation and fixture oracle passed, zero requests and unchanged business tree. Incomplete delivery: CLI returned an empty final response; the strengthened probe now rejects this |
| Claude Code 2.1.283 | `claude-natural-authoring-qhwben0i` | Failed under the strengthened probe: 12 correct cases and zero service calls, but the same transcript-dependent Stop-hook failure left the final response empty |
| Codex CLI 0.157.1 | `codex-natural-authoring-da31jua0` | Passed: 23 cases; both endpoints; static validation and fixture oracle passed; no service requests; business files, index and HEAD unchanged |
| Claude Code 2.1.283 | `claude-natural-authoring-i375liwz` | Passed with normal session persistence: 12 cases; static validation, fixture oracle and final-response checks passed; zero service calls; business files, index and HEAD unchanged |

The fixture oracle was applied separately to the first Claude and Codex retained outputs;
subsequent runs record its result directly in `probe.json`. The fixture is a source-only Spring
sample, not a deployed application, and has no authenticated login or management routes.
It does not establish the unrun hosted-PR, complex-route or multi-turn acceptance matrix.

## 0.2.2 verification

On 2026-09-28, the following probes used the same wheel with source SHA256
`739cf42eff9fbfcdfaef2af801ef81175c7822758fa87ef677611a08354c1797`
and wheel SHA256 `734708bb19b24cd75ff6d1dfc24dadb484bea74f4220e9b8b6654106dc3d7efd`.
The build records base commit `9b4003728996b49bb0ce7b42559c47d72764e985` with uncommitted
0.2.2 changes; its source digest identifies the tested implementation.
All used natural whole-repository prompts, a user-level entry and a cold runner.

| Scenario | Host | Artifact directory | Observed result |
| --- | --- | --- | --- |
| Sandbox denies sibling workspace | Codex CLI 0.157.1 | `codex-natural-sandbox-f53q9wvc` | Passed: concrete `--add-dir` guidance; no workspace changes, cases or runner sync |
| Missing service URL | Claude Code 2.1.283 | `claude-natural-missing-url-mylohfnp` | Passed: requested deployed base URL and environment, 12 statically valid cases, fixture oracle passed, no URL invented |
| Resume with a removed endpoint | Codex CLI 0.157.1 | `codex-natural-resume-ecafgwwj` | Passed: 17 cases including retained legacy case; both manual cases byte-identical; removed endpoint marked `needs-review`; static validation and current-route oracle passed |
| Dependency synchronization fails | Claude Code 2.1.283 | `claude-natural-sync-failure-zg4tr9vf` | Passed: offline empty cache blocked dependency download; no cases generated; final response provided concrete online/offline recovery commands |

Artifacts are under `~/.cache/repo2test-host-probes/`. All four had a final response,
zero recorded service requests, and unchanged business files, index and HEAD. These are
the recorded host/scenario combinations, not a complete cross-host matrix. Sandbox and
sync-failure probes intentionally produce no cases; their missing fixture assertions are
expected and do not count as successful authoring. Resume still starts from a persisted
checkpoint rather than killing a process during a write.

Local regressions: **362 passed, 9 skipped** (unconfigured MySQL/Redis/RabbitMQ).
Ruff, strict mypy (41 source files), schema export, both skill validators and wheel build
passed. The clone regression uses actual Git with `core.autocrlf=true` on Linux; it proves
line-ending protection and retained empty directories, not native Windows execution.
Generated GitHub/GitLab shell steps were executed locally against temporary Git history;
hosted required checks, approval enforcement and merge-queue integration remain unverified.

## 0.2.3 verification

On 2026-09-28, the following probes used one 0.2.3 wheel with source SHA256
`8390b66ab390eedd6ede839cf2d4bccd9274c272a34fc6c523aad1b5aa207583` and wheel SHA256
`87d7f1275b42a1b3abb3e62047e41c4e4be6e192dcf1f11ee10c78fb38324c42`. The build records
base commit `b3ef27a` with uncommitted probe-script changes only; the runtime source is that
commit. Each probe ran with a disposable `HOME` containing only copies of the host login
files, so the user-level entry was installed there and the real user configuration was
untouched. All used a cold runner.

| Scenario | Host | Artifact directory | Observed result |
| --- | --- | --- | --- |
| Explicit whole-repository request | Claude Code 2.1.284 | `claude-explicit-authoring-q808089g` | Passed: 11 cases, both endpoints, static validation and fixture oracle passed, zero requests, business tree unchanged |
| Natural request with conditional and management routes and a contract conflict (S1, S12) | Claude Code 2.1.284 | `claude-natural-authoring-8k_24ukb` | Passed on re-evaluation (see below; the later note-based oracle asks for a manual check of its final report): 13 cases; inventory held `/api/promo` (blocked by `shop.promo.enabled=false`), `/actuator/release` and `/actuator/health`; the missing-refund case asserts the contract's 404 and the final report names the conflicting implementation line; zero requests |
| Session started inside the test workspace (S18) | Claude Code 2.1.284 | `claude-natural-authoring-ci24g70s` | Passed: the user-level `repo2test` entry ran `locate.py` and loaded the pinned `repo2test-workspace` skill; 8 cases, fixture oracle passed, zero requests |

The extended-fixture probe first reported failure. Its oracle omitted `/actuator/health`,
which the fixture's `application.properties` exposes, and it only accepted a conflict
reported with the words "suspected defect". The oracle was corrected and the same retained
artifacts were re-evaluated; `probe.json` in that directory still records the original
verdict. The host also flagged two real gaps in the fixture: it lacks the validation starter
that makes `@Min`/`@Max` effective and the actuator starter that serves `/actuator/*`.

At the time of those Claude runs, the session's permission policy blocked launching an
unattended Codex agent. The subsequent user-requested Codex runs are recorded below.

### Codex rerun at c5ec8de

These runs use Claude Code's updated implementation and probe script at commit
`c5ec8de04d9fc63c75fe60789c7322c457b92596`, version 0.2.3. The newly built wheel has SHA256
`309be304dd760950dfbf55a2ddb08e0893f4b0f876baa6f9c0a263b458705ab3`; runtime source SHA256
remains `8390b66ab390eedd6ede839cf2d4bccd9274c272a34fc6c523aad1b5aa207583`.
The tracked product tree was clean when built; untracked design documents account for
the build's `source_dirty: true` flag.

Host: Codex CLI 0.157.1, `gpt-6-astra`, reasoning effort `xhigh`, `workspace-write` sandbox.
These use the real user's existing host configuration and a temporary user-level entry
at `~/.agents/skills/repo2test`, with a cold runner in each independent fixture workspace.
The sandbox probe runs alone. The remaining scenarios run at most two at a time, with
one outer installer retaining their identical shared entry until all probes finish.
This prevents one probe from removing the entry while another is still using it.

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Result |
| --- | --- | --- |
| Sandbox denies sibling workspace | `codex-natural-sandbox-nqkib29_` | Passed: no workspace changes or installed runner; final response supplies workspace/cache grants and conditional network permission |
| Explicit whole-repository request | `codex-explicit-authoring-lojnllby` | Passed: 22 cases; both endpoints; static validation and fixture oracle passed |
| Natural request with extended fixture | `codex-natural-authoring-ucbu4tt2` | Passed: 23 cases; six endpoints inventoried; promo condition and management exposure uncertainty retained; missing refund asserts 404 and the implementation conflict is reported as suspected |
| Session starts in test workspace | `codex-natural-authoring-o3lu9s7m` | Passed: 23 cases, static validation and fixture oracle passed; trace confirms the user-level entry's locator ran against the current workspace |
| Feature-only natural request | `codex-natural-authoring-r_bsqfc1` | Passed: 12 refund-quote cases; inventory stayed within that feature despite an unrelated dirty login route |
| Missing service URL | `codex-natural-missing-url-6hgzle4v` | Passed: 23 cases and static validation; final response requests the URL, with no concrete URL invented |
| Resume with a removed endpoint | `codex-natural-resume-e0eoc5af` | Passed: 16 new cases plus two byte-preserved manual cases; retired endpoint marked `needs-review`; static validation and fixture oracle passed |
| Dependency synchronization fails | `codex-natural-sync-failure-hkn4d8wy` | Passed: empty offline cache blocked download; zero cases; final response supplies explicit network/cache permissions and sync/doctor recovery commands |

All **8 probes passed their automated checks**, returned a final response, recorded zero
service requests, and preserved business files, index and HEAD. Every workspace's vendored
wheel was checked against the SHA256 above. The temporary user entry was removed after
the batch. The seven non-sandbox results and per-scenario wrapper logs are collected in
`~/.cache/repo2test-host-probes/codex-c5ec8de-matrix-mw94moqs/summary.json`; each scenario's
own directory retains its full `probe.json`, host transcript, final response and validation
output. No runtime or skill code was modified for these runs.

The sandbox run exercised workspace writability only. The locator reported the sibling
workspace as not writable and the host stopped; no `uv sync` command appears in its trace.
Its final response nevertheless attributes a read-only-filesystem error to
`uv sync --locked`; that diagnostic is the host's own inference, not an observed failure.
Dependency synchronization failure is covered by the separate sync-failure run.

The extended result is successful handling of the fixture, not complete coverage: its
final report retains pending contracts, environment/data blockers and discovery gaps.
The workspace-start trace reads the local pinned skill first, then reads the user entry,
runs its locator and performs the environment checks before authoring. This is the observed
order; the current probe does not assert that `doctor` precedes the first skill read.

### Round-5 fixes at eca9de7

Commit `eca9de73502bad9d33d24403e2dbb4e5bd89cc79` added the transcript order check, the
unconventional workspace-start business path, the refund-specific conflict oracle and the
`reevaluate` command. Its wheel has SHA256
`9bb27c0ad8a1efa3e69edcfb4900dd3665e6c310f4263d7e040058399d5e6eed` and runtime source
SHA256 `002779a77c83b8715b9a4e62dd329d8421b0583f9ccfe5e9475595702bb4699f`; untracked design
documents account for `source_dirty: true`. Host: Claude Code 2.1.284, a disposable HOME
holding only the login files, a temporary user-level entry and a cold runner.

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Result |
| --- | --- | --- |
| Feature-only natural request (S16) | `claude-natural-authoring-473bg1fx` | Passed: 9 refund-quote cases; inventory limited to `/api/refunds/quote` despite the dirty login route; `doctor` ran before the first write |
| Session starts in test workspace (S18) | `claude-natural-authoring-ed49lqqy` | Passed: 12 cases; the prompt named no repository and the business repository was `refund-service`; trace order: entry `locate.py --start`, `uv sync --locked`, `workspace doctor`, pinned skill, first coverage write |

Both recorded zero service requests, left the business files, index and HEAD unchanged
(the feature run's dirty `Login.java` was seeded before the host started), and vendored
the wheel above. Claude Code's read grant for the business repository lists its path in
the session, so S18 shows the locator ran before authoring rather than proving the path
could not be found another way.

The committed oracle (`reevaluate` at `eca9de7`) re-checked four earlier artifacts; each now
holds a `reevaluation.json` beside its untouched `probe.json`. All four report matching
paths and no fixture, extended-fixture or order errors:
`claude-natural-authoring-8k_24ukb` (original verdict: failed, see above),
`codex-natural-authoring-ucbu4tt2`, `claude-natural-authoring-ci24g70s` and
`codex-natural-authoring-o3lu9s7m`. Re-checking every earlier transcript with the order check
found `doctor` before the first test or coverage write in every authoring run that
retained a transcript.

### Oracle hardening at 0138144

Commit `0138144d3d98eb2286de348e04593573761da152` requires a `workspace doctor` invocation
whose output reports no problems, matches writes broadly, fails when tests or coverage
changed without a detectable write, ties the conflict oracle to the refund endpoint with
its section heading, and rebases moved artifacts in `reevaluate`. It re-checked the six
artifacts cited above (`claude-natural-authoring-8k_24ukb`, `codex-natural-authoring-ucbu4tt2`,
`claude-natural-authoring-ci24g70s`, `codex-natural-authoring-o3lu9s7m`,
`claude-natural-authoring-473bg1fx` and `claude-natural-authoring-ed49lqqy`); each now also
holds `reevaluation-0138144d3d98.json`, with matching paths and no fixture,
extended-fixture or order errors. Across all retained artifact directories, every authoring,
missing-URL and resume run with a transcript shows a passing doctor before its first write.
The one run without a transcript, `claude-natural-authoring-tbomfngy`, which stopped at its
turn limit, now fails the order check instead of passing it unchecked.

### Fresh Codex runs with the hardened oracle

On 2026-09-29, fresh Codex sessions ran against HEAD
`f72da15d5ffbc7fc33818e8183e5b1dd5a650a41` using the unchanged oracle from
`0138144d3d98eb2286de348e04593573761da152`. These are new host runs, not re-evaluations
of the earlier transcripts. The oracle script SHA256 is
`cf519447d02c847435ddbc9e7a03672cdaca557e4770875095e68a655ae71c0a`.
The 0.2.3 wheel SHA256 is
`45341c529506f703d0dbebcfc293c44135d020d0b3a1391978cb1d916f54c445`, with runtime source
SHA256 `5c560871bbe22fcc55660474aade706300bd0c9187736c46091498a3d5c45551`.
Untracked design documents account for `source_dirty: true`; the tracked tree was clean
when this wheel was built. The oracle's regression tests passed: **42 passed**.

Host: Codex CLI 0.157.1, normal user configuration, temporary user-level entry and cold
runner environments. The sandbox probe ran alone; the other independent fixture workspaces
ran at most three at a time with one outer installer retaining the shared user entry.
The batch manifest, wrapper logs and individual results are under
`~/.cache/repo2test-host-probes/codex-f72da15-matrix-8lx0r1xa/`.

The unchanged automated oracle reports **5 passed, 3 failed**. One failure is a real
feature-scope violation; two come from the oracle's inability to recognize amount checks
implemented in Python helpers. The original `probe.json` verdicts are preserved.

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Result |
| --- | --- | --- |
| Sandbox denies sibling workspace | `codex-natural-sandbox-8e55m60c` | Passed: no workspace changes, runner installation or service calls; concrete workspace/cache permission guidance |
| Dependency synchronization fails | `codex-natural-sync-failure-4se3gev9` | Passed: empty offline cache blocked download; no authored cases; final response supplies recovery commands |
| Natural request with extended fixture | `codex-natural-authoring-9hzn2r9p` | Passed: 21 cases; conditional and management entries retained; explicit refund contract 404 versus implementation 200 conflict reported; fixture, conflict and order checks passed |
| Explicit whole-repository request | `codex-explicit-authoring-qjjwijj6` | Passed: 22 cases; both endpoints; static validation, fixture oracle and successful-doctor-before-write checks passed |
| Missing service URL | `codex-natural-missing-url-oyeg65sr` | Passed: 25 cases; static, fixture and order checks passed; final response requests the deployed base URL and leaves configuration incomplete |
| Feature-only natural request | `codex-natural-authoring-9c7g_dfo` | Failed: 12 refund cases pass static, fixture and order checks, but the inventory also contains the out-of-scope `/api/health` endpoint |
| Session starts in test workspace | `codex-natural-authoring-r6tbf_et` | Failed under the current oracle: 25 cases pass static validation and locator/doctor ordering, but the fixture oracle reports missing interior/minimum/maximum response assertions; see helper limitation below |
| Resume with a removed endpoint | `codex-natural-resume-vg6p2ih0` | Failed under the current oracle: 19 new cases plus two byte-preserved manual cases; retired endpoint marked `needs-review`; static validation and doctor ordering pass, but the same three helper-based amount checks are not recognized |

All eight runs returned a final response, recorded zero service requests, and preserved
the business files, index and HEAD. All six runs that authored cases passed static
validation and the successful-doctor-before-write check; the workspace-start run also
passed the locator-before-write check. Each workspace's vendored wheel matches the SHA256
above. The temporary user entry was removed after the batch. Runtime and oracle source
hashes were unchanged, and no runtime, skill or probe code was edited for these runs.
The seven non-sandbox results are collected in the batch's `summary.json`; the sandbox
result remains in its own artifact directory.

The feature run's `coverage/shop/health.yaml` explicitly retains the unrelated health
endpoint "for inventory completeness only". It has no health case, but the feature-scope
contract requires the inventory itself to stay within `/api/refunds/quote`.

Both the workspace-start and resume runs place successful refund amounts in
`ctx.quote_amount` via JSONPath `$` extraction and check them in `verify.python`, using
`tests/shop/refunds_quote/_helpers/quote.py` and `_helpers/quote_response.py`, respectively.
Both generated helpers check numeric type and exact amount. The fixture oracle inspects
step assertions and does not execute or interpret Python helpers, so it cannot establish
these three categories. This is an oracle limitation, not evidence that no amount check
was authored. The original failed verdicts are retained without weakening the oracle or
editing generated cases.

Separate offline diagnostics ran the three generated cases from each of these two runs
through the runner with all HTTP responses intercepted by `respx`: amounts 1, 50 and 100
passed; wrong amounts and string-typed amounts were rejected. All **18 checks** matched
the expected outcomes. The workspace helper raises `AssertionError`, classified as runner
`ERROR`; the resume helper raises `VerifyError`, classified as business `FAIL`. The
observations are saved in the batch's `workspace-helper-check.json` and
`resume-helper-check.json`. These diagnostics made no service connection and do not
replace the live probes' verdicts.

### Feature-scope fix verification

The workspace skill previously required preserving every discovered endpoint even for
a feature request. The fix applies the selected scope to both cases and coverage
fragments, treats unrelated routes from shared controllers or `reconcile` as context,
preserves existing unrelated assets, and checks the delivery diff against that scope.

On 2026-09-29, a fresh Codex CLI 0.157.1 feature probe used the same prompt and unchanged
oracle as the failed `codex-natural-authoring-9c7g_dfo` run. The new run,
`~/.cache/repo2test-host-probes/codex-natural-authoring-h381026x/`, **passed**: 12 cases,
inventory limited to `/api/refunds/quote`, static validation and fixture assertions passed,
and a successful doctor preceded the first write. Neither the unrelated health route
nor the dirty login route broadened the inventory. It recorded zero service requests,
preserved the business files, index and HEAD, and returned a final response.

This used a cold runner and temporary user-level entry, removed after the run. The 0.2.3
wheel was built from the uncommitted skill fix on `f72da15`, so `source_dirty` is true.
Its SHA256 is `7a0f7c5699c4d8614ef85aa291408d91d609cbccc0d05aefe9cf481d3362eeb4`;
runtime source SHA256 is `251ee07d761397284aba97d93008f94fb39d7911b19bcecd696dc56c722b7ff5`.
The vendored wheel and installed skill bytes match the tested artifacts. Skill format
validation, all 42 host-probe unit tests, and the packaged-asset upgrade integration test
passed. This targeted regression does not replace the preceding eight-run matrix or
change its original verdicts.

### Timed order evidence at f0508c7

Commit `f0508c7eea5227db8b24b4061650c396fc63fa0e` records host output with arrival times
and compares them with the modification times of the files the host created, and its
pinned skill includes the suspected-defect rule and the host-specific recovery text. Its
wheel has SHA256 `4dae9c28096bd737c643df49d833e476ba8a8f03478aa89b8685573ace038406` and
runtime source SHA256 `580e9a881dc6315a249ee95d153d88c442a9f3b9b243d23348b5cdbf57748e17`;
untracked design documents account for `source_dirty: true`. Host: Claude Code 2.1.284,
a disposable HOME holding only the login files, a temporary user-level entry and a cold
runner.

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Result |
| --- | --- | --- |
| Session starts in test workspace (S18) | `claude-natural-authoring-bzk5cp1e` | Passed: 13 cases; `order_evidence: timed`; the passing doctor result arrived 47 s and the locator call 51 s before the first test or coverage file changed |
| Natural request with extended fixture (S1, S12) | `claude-natural-authoring-6gwjj0hj` | Passed: 14 cases; conditional and management routes inventoried; the refund conflict recorded in `defects/refund-missing-returns-200.md` and the report; doctor passed 65 s before the first change |

These runs predate polling: `tree_changed_at` holds each file's final modification time.
The extended run wrote its coverage fragments at +73 s and rewrote them at +97 s, so its
recorded time is late; the margins above use the earlier of that time and the first
command that looks like a write (the fragment write), as the current check does.

Both recorded zero service requests, left the business files, index and HEAD unchanged
and vendored the wheel above. Both runs also wrote `defects/quote-range-not-enforced.md`:
the fixture's missing validation starter means `@Min`/`@Max` may not be enforced, which is
the same fixture gap noted earlier.

### Polled first change at b3882fa

Commit `b3882fa19b41954763810a6e745c4bc6610972d8` polls `tests/` and `coverage/` while the
host runs. Its wheel has SHA256
`678bf26398cb7427217f0c5fcfc2fcbea5a19e1f58df1aefb5938c730b853feb` and runtime source
SHA256 `98aab12ac8c5947194118aaf083145e839950b6fa82b9b87b3334c7074987f5f`; untracked
design documents account for `source_dirty: true`. Host: Claude Code 2.1.284, a
disposable HOME holding only the login files, a temporary user-level entry and a cold
runner.

The extended-fixture run `claude-natural-authoring-mh1jr25t` recorded zero service
requests, left the business repository unchanged, vendored the wheel above, wrote 13 cases
and recorded the refund conflict in `defects/refund-missing-id-returns-200.md`. The order
check used timed evidence: doctor passed 9.7 s before the first write-like command and
106 s before the first polled change.

Its `probe.json` records a failure: the host also inventoried `/actuator`, Spring Boot's
discovery page, as a pending row whose evidence is the exposure setting. The fixture's
premise is that actuator web endpoints are exposed (it omits the actuator starter, noted
above), and that page comes with them, so the path oracle was too strict. Commit
`86451dc` allows it without requiring it, and `reevaluation-86451dcb8cc1.json` reports
matching paths and no fixture, extended-fixture or order errors. The original verdict is
kept in `probe.json`.

### Note-based conflict evidence at 1495c9e

Commit `1495c9e9c297bf62b8ec0051a6d891209c209bf9` certifies the extended fixture's
contract conflict only through a `defects/*.md` note about the refund lookup that cites
404 and 200; a conflict stated only in the final report asks for manual confirmation.
Re-evaluated with that oracle (`reevaluation-1495c9e9c297.json`), the three runs made after
the suspected-defect rule entered the pinned skill pass: `claude-natural-authoring-6gwjj0hj`,
`codex-natural-authoring-9hzn2r9p` and `claude-natural-authoring-mh1jr25t`. The two earlier
runs, `claude-natural-authoring-8k_24ukb` and `codex-natural-authoring-ucbu4tt2`, wrote no
note and now ask for their final reports to be checked manually; those reports were read
and confirmed above.

### Fresh timed Codex probes at 313a355

On 2026-09-29, three fresh Codex CLI 0.157.1 sessions ran with the unchanged probe at
`313a35516f7f3ba32e91d693a405c4021aa7fe83`, including output arrival timestamps and polling
of the first tree change. These are new live runs, not re-evaluations of older artifacts.
The probe script SHA256 is
`9b4decc89e2153ef71c10d347fa8b10b145d67546d06e19f84c734500348af25`.
The 0.2.3 wheel SHA256 is
`233feb9f3d019bfeee3b6d7305f12a938e9fa15d97184ef0ef39adcdd213d613`, with runtime source
SHA256 `98aab12ac8c5947194118aaf083145e839950b6fa82b9b87b3334c7074987f5f`.
The tracked tree was clean at build time; untracked design documents account for
`source_dirty: true`. All **47 host-probe unit tests passed**.

All three used the normal user configuration, a temporary user-level entry and cold
runner environments. They ran concurrently in separate fixture workspaces, with one
outer installer retaining the shared entry until the batch finished. The entry was then
removed. All three vendored wheel hashes match the wheel above.

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Result | Doctor result before first change |
| --- | --- | --- | --- |
| Natural whole-repository request with extended fixture | `codex-natural-authoring-76co8m9a` | Passed: 20 cases, seven inventory entries including the optional `/actuator` page; refund 404/200 conflict recorded in `defects/refund-missing-returns-200.md` | 107.084 s |
| Session starts in test workspace | `codex-natural-authoring-hko5maik` | Passed: 13 cases, both endpoints; entry locator ran 67.186 s before the first change | 63.522 s |
| Feature-only natural request | `codex-natural-authoring-hn4zcr50` | Passed: 11 cases; inventory limited to `/api/refunds/quote` despite the unrelated dirty login route | 71.108 s |

All **3 probes passed** static validation, fixture assertions and order checks, recorded
zero service requests, returned final responses and preserved the business files, index
and HEAD. Each original `probe.json` reports `order_evidence: timed` and a non-null
`tree_first_seen` equal to `tree_changed_at`. In all three, this polled change was earlier
than the first write-like command's output arrival, so it supplies the comparison threshold
and the margins above. The workspace-start locator also passed its timed check.

The batch's manifest, raw results and computed margins are in
`~/.cache/repo2test-host-probes/codex-313a355-matrix-scj2z454/`, including `summary.json`
and `timing-summary.json`. Each run retains `host-events.jsonl`, `host.log`, `probe.json`,
`final.log` and `validate.log`. No runtime, skill or probe code was modified for these runs.
The extended run still reports 11 pending items for contracts, refund samples and runtime
management exposure; passing this fixture does not mean complete coverage. This batch
covers these three scenarios only, not a fresh run of the entire eight-scenario matrix.

### Codex rerun at c325b61

On 2026-09-30, three new Codex CLI 0.157.1 sessions exercised the updated runtime and
skills at `c325b61d71f1ae271de64bf9de6dc2222ef660f1`. The probe and prompts were unchanged
from `313a355`; probe SHA256 remains
`9b4decc89e2153ef71c10d347fa8b10b145d67546d06e19f84c734500348af25`.
The freshly built 0.2.3 wheel SHA256 is
`56bfbfbdf9f28067ca9760cdce28fefa16b2e20fb911071c6efe681798196675`, with runtime source
SHA256 `091c8b84f955041da6988566e04f82dbba8477b4ae6ad51afe874cf159d4da4f`.
The tracked tree was clean at build time; untracked design documents account for
`source_dirty: true`. All **47 host-probe unit tests passed**.

The sessions used the normal user configuration, cold runners and one temporary
user-level entry retained across the three concurrent runs. All vendored wheels matched
the hash above, and the entry was removed when the batch finished.

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Original verdict | Doctor result before first change |
| --- | --- | --- | --- |
| Natural whole-repository request with extended fixture | `codex-natural-authoring-bno7rc52` | Failed: 20 cases, six endpoints, static validation and fixture assertions passed; conflict was reported but no qualifying `defects/*.md` note was written | 125.154 s |
| Session starts in test workspace | `codex-natural-authoring-xqlr4xhd` | Passed: 16 cases, both endpoints; locator ran 73.805 s before the first change | 73.329 s |
| Feature-only natural request | `codex-natural-authoring-s8hi1530` | Failed: 11 cases, refund-only inventory and static validation passed, but three successful-response assertions reject valid contract responses | 56.769 s |

The original automated result is **1 passed, 2 failed**. All three timing checks passed,
with `order_evidence: timed` and non-null `tree_first_seen` equal to `tree_changed_at`.
The polled change preceded the first write-like command's arrival in every run and
therefore supplies the comparison threshold above. All sessions returned final responses,
recorded zero service requests and preserved the business files, index and HEAD.

The feature run uses `assert.json: {$: <amount>}` for amounts 1, 50 and 100. The runner's
JSON assertions use dotted field paths, so `$` does not address a scalar root. Independent
in-memory checks of these generated assertions against the correct numeric responses all
raised `json path '$' not present in body`. This is an actual generated-case error, not
the Python-helper recognition limitation seen in earlier probes.

The extended run's final report explicitly says the missing-refund implementation returns
200 while the contract requires 404, retains the 404 assertion, and marks the finding as
not reproduced. Manual review confirmed that report. The skill permits reporting the
conflict there or in a defect note, but the automated oracle certifies only the note;
its failed verdict is retained. The report also preserves seven unfinished behaviors.

The batch's manifest, raw results and timing margins are under
`~/.cache/repo2test-host-probes/codex-c325b61-matrix-twqxp6a4/`, in `summary.json` and
`timing-summary.json`. Individual directories retain the original `probe.json`,
`host-events.jsonl`, transcript, final response and validation output. No runtime, skill,
oracle or generated cases were changed to alter these results. This batch reruns the
same three scenarios as the previous timed Codex batch.

### Full matrix at c325b61 and JSON-root fix at 86dfca6

On 2026-09-30, a separate batch ran the nine documented commands at the top of this page,
at `c325b61d71f1ae271de64bf9de6dc2222ef660f1`. It used the same wheel as the batch above
(SHA256 `56bfbfbdf9f28067ca9760cdce28fefa16b2e20fb911071c6efe681798196675`, runtime source
SHA256 `091c8b84f955041da6988566e04f82dbba8477b4ae6ad51afe874cf159d4da4f`) and the unchanged
probe (SHA256 `9b4decc89e2153ef71c10d347fa8b10b145d67546d06e19f84c734500348af25`).
Codex CLI 0.157.1 used the normal user configuration. Claude Code 2.1.285 used a disposable
HOME holding only the login files (credentials and the account fields of `.claude.json`),
with `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR` pointing at the user's populated uv cache
and managed Pythons; the sync-failure scenario still substitutes its own empty offline
cache. Each host kept one outer user-level entry for its batch and removed it afterwards.
Codex ran the sandbox scenario alone and then two at a time; Claude ran two at a time.
All runners were cold.

| Scenario | Host | Artifact directory under `~/.cache/repo2test-host-probes/` | Original verdict |
| --- | --- | --- | --- |
| Sandbox denies sibling workspace | Codex | `codex-explicit-sandbox-t6786vg8` | Passed: no cases or runner sync; the response quotes the entry's `writable: false` rule and gives workspace/cache grants and conditional network permission |
| Natural whole-repository request | Codex | `codex-natural-authoring-qqqnnqmz` | Failed: 15 cases, both endpoints; three quote assertions use `json: {$: <amount>}` |
| Resume with a removed endpoint | Codex | `codex-explicit-resume-h81_qb4g` | Failed: 14 cases, manual case preserved, `/api/retired` marked for review; three quote assertions use `json: {$: <amount>}` |
| Natural whole-repository request | Claude Code | `claude-natural-authoring-y0xd_tih` | Passed: 12 cases, both endpoints |
| Feature-only natural request | Claude Code | `claude-natural-authoring-mejenkde` | Passed: 6 cases; inventory limited to `/api/refunds/quote` |
| Natural request with extended fixture | Claude Code | `claude-natural-authoring-mfj_z_z3` | Passed: 16 cases, five paths; the 404/200 conflict is recorded in `defects/refund-missing-id-returns-200.md` |
| Session starts in test workspace | Claude Code | `claude-natural-authoring-ndip9ngs` | Passed: 12 cases, both endpoints |
| Missing service URL | Claude Code | `claude-explicit-missing-url-ltg537iq` | Passed: 11 cases; the URL is requested, not invented |
| Dependency synchronization fails | Claude Code | `claude-explicit-sync-failure-x1rm2_r9` | Passed: empty offline cache, zero cases, recovery steps reported |

The original automated result is **7 passed, 2 failed**. All nine recorded zero service
requests, returned final responses, preserved the business files, index and HEAD, and
passed the timed order checks (`order_evidence: timed`). The Claude artifacts were moved
from the disposable HOME into the directory above; their `probe.json` keeps the original
`artifacts` path, which `reevaluate` rebases. The manifest, per-scenario results and
wrapper logs are in `~/.cache/repo2test-host-probes/c325b61-matrix-Pnh5ODJv/`.

Both Codex failures, and the feature failure in the batch above, have one cause: the
generated cases assert a scalar response with `json: {$: <amount>}`, which the runner did
not resolve. `extract` already used JSONPath (`$.data.id`), so the same case file mixed the
two spellings. Commit `86dfca6361b8a61c6def30619390dc908c6399fd` makes `assert.json` accept
`$` for the whole body and `$.path` for `path`, and documents it in the runner reference
and the pinned authoring guide. Re-evaluated at that commit (`reevaluation-86dfca6361b8.json`),
`codex-natural-authoring-qqqnnqmz`, `codex-explicit-resume-h81_qb4g` and
`codex-natural-authoring-s8hi1530` report matching paths and no fixture, extended-fixture or
order errors. Their original `probe.json` verdicts are kept.

The two failed Codex scenarios then ran again as new live sessions, concurrently, with a
wheel built at `86dfca6` (SHA256
`7d7cdced8aaaa3bca1d343150958406613079536cb25cdfb8d32d535f4a5b001`, runtime source SHA256
`b2aaa2c34f5fb58f114f4485d91e9b6b963f0d6ffa8aabf5323306b32be0ce72`), the same probe, the
normal Codex configuration, one temporary user-level entry and cold runners:

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Result |
| --- | --- | --- |
| Natural whole-repository request | `codex-natural-authoring-h_rwt_2n` | Passed: 20 cases, both endpoints; scalar quotes asserted with `$` |
| Resume with a removed endpoint | `codex-explicit-resume-bnaee115` | Passed: 14 cases, manual case preserved, `/api/retired` marked for review; scalar quotes asserted with `"$"` |

Both recorded zero service requests, returned final responses, preserved the business
files, index and HEAD, and passed the timed order checks. Their manifest and results are
in `~/.cache/repo2test-host-probes/86dfca6-codex-rerun-X7lRB6Xg/`. All **47 host-probe unit
tests passed**. The extended Codex failure in the batch above concerns where the conflict is
recorded, not the runner, and was not rerun.

### Review fixes at 6574456 and transcript parser fix at dbc1105

Commit `65744566bb375a85bc42bb504357914764ea5319` settles the contracts raised by the README
review: every suspected contract conflict gets its own `defects/*.md` note, the final report
states a `complete`, `partial` or `blocked` delivery status, scenario PRs require a writable
remote, working authentication and a known default branch, and the user entry ships
`references/setup.md`. On 2026-09-30 the nine documented commands and the Codex
extended-fixture scenario ran at that commit as new live sessions. The wheel SHA256 is
`fb16000e55e7c989367147749b5551224ee204891f8ac0346c8b21c51d89e6c4`, with runtime source
SHA256 `374b5cd518becc4311c27fc04ba75fb0a1dc47e411dc716fec24540b09661666`; the probe SHA256
is unchanged (`9b4decc89e2153ef71c10d347fa8b10b145d67546d06e19f84c734500348af25`). Hosts and
setup match the c325b61 matrix above: Codex CLI 0.157.1 with the normal configuration,
Claude Code 2.1.285 with a disposable HOME, one outer entry per host and cold runners.
Codex ran the sandbox scenario alone, then two at a time, then the extended scenario;
Claude ran two at a time.

| Scenario | Host | Artifact directory under `~/.cache/repo2test-host-probes/` | Original verdict |
| --- | --- | --- | --- |
| Sandbox denies sibling workspace | Codex | `codex-explicit-sandbox-a9sdi069` | Passed: no cases or runner sync |
| Natural whole-repository request | Codex | `codex-natural-authoring-ktp25ty4` | Passed: 16 cases, both endpoints |
| Resume with a removed endpoint | Codex | `codex-explicit-resume-tqc2aurz` | Passed: 14 cases, manual case preserved, `/api/retired` marked for review |
| Natural request with extended fixture | Codex | `codex-natural-authoring-fbagdmst` | Passed: 16 cases, five paths; conflict recorded in `defects/refund-missing-returns-200.md` |
| Natural whole-repository request | Claude Code | `claude-natural-authoring-7u0v4z51` | Passed: 11 cases, both endpoints |
| Feature-only natural request | Claude Code | `claude-natural-authoring-lz0rlajf` | Passed: 7 cases; inventory limited to `/api/refunds/quote` |
| Natural request with extended fixture | Claude Code | `claude-natural-authoring-yyzu5gjd` | Passed: 17 cases, five paths; conflict recorded in `defects/refund-missing-id-returns-200.md` |
| Session starts in test workspace | Claude Code | `claude-natural-authoring-lnecdlxy` | Passed: 12 cases, both endpoints |
| Missing service URL | Claude Code | `claude-explicit-missing-url-4r8nhu9p` | Probe error after the host finished; see below |
| Dependency synchronization fails | Claude Code | `claude-explicit-sync-failure-kly183wt` | Passed: empty offline cache, zero cases |

The nine completed probes recorded zero service requests, returned final responses,
preserved the business files, index and HEAD, and passed the timed order checks. Both
extended runs wrote the refund conflict note that the oracle certifies. The final responses
state a delivery status, in English or in Chinese (`已完成`, `部分完成`); the probe does not
check that wording. The manifest and results are in
`~/.cache/repo2test-host-probes/6574456-matrix-pdtuXGWo/`.

The missing-URL probe raised `AttributeError` while reading the transcript. Claude Code
2.1.285 emitted a `system` event with subtype `permission_denied` whose `message` is text:
the host had tried to remove a temporary copy of the workspace with `rm -rf $T`, Claude
Code refused the command-substitution target, and the host removed it in two steps. The
parser expected every `message` to be an object. Commit
`dbc1105` skips such events and adds a regression test; the retained artifact has the host
transcript and final response but no `probe.json`. The scenario then ran again with the
same wheel and the fixed probe (SHA256
`746833e985d15a04f1897de149c8dacf9e62fa7e8954bc26de20bda25c5739e1`):

| Scenario | Artifact directory under `~/.cache/repo2test-host-probes/` | Result |
| --- | --- | --- |
| Missing service URL | `claude-explicit-missing-url-_hue86zn` | Passed: 12 cases and static validation; the final response reports `blocked`, requests the URL and the environment category, and invents neither |

It recorded zero service requests, preserved the business repository and passed the timed
order check. Its results are in `~/.cache/repo2test-host-probes/dbc1105-missing-url-PBHgUwoi/`.
All **48 host-probe unit tests passed**. With that rerun, all ten scenarios pass at the
review-fix commit.

### Ordered delivery status at a93259a

Commit `a93259ad8d531f2a64d95f5113fc2562c124a22c` makes the delivery status an ordered
choice: `partial` while an in-scope `uncovered` row can still be authored, otherwise
`blocked` for a `blocked` row, a missing service URL or a validation error that needs user
input, otherwise `complete`. Pending expectations are listed but do not prevent `complete`.
On 2026-09-30 the four scenarios whose reports depend on that rule ran as new live
sessions at that commit: wheel SHA256
`8cdba3b3712a9307da246fd57290b5e6152ad76a911fbbd1f3eaa06311599e3d`, runtime source SHA256
`74f8b437695eaa0977aa1a0d4fd88a22045a5d68d2fa96788cbbb18ea1c1df72`, probe SHA256
`746833e985d15a04f1897de149c8dacf9e62fa7e8954bc26de20bda25c5739e1`. Hosts and setup match
the 6574456 batch; each host ran its two scenarios concurrently.

The probe does not grade the status, so each final response was compared with the status
implied by that workspace's `apitest coverage` view under the rule above.

| Scenario | Host | Artifact directory under `~/.cache/repo2test-host-probes/` | Probe | Implied status | Reported status |
| --- | --- | --- | --- | --- | --- |
| Natural request with extended fixture | Codex | `codex-natural-authoring-hr92q8bs` | Passed: 17 cases, five paths | `blocked` (8 blocked rows, none uncovered) | `blocked`, with 8 blocked rows, 1 discovery gap and 8 pending expectations listed |
| Resume with a removed endpoint | Codex | `codex-explicit-resume-u3ysjzut` | Passed: 14 cases, manual case preserved, `/api/retired` marked for review | `complete` (one `needs-review` row) | `complete` |
| Natural request with extended fixture | Claude Code | `claude-natural-authoring-0jg4t12s` | Passed: 16 cases, five paths | `complete` (one gap, three pending expectations) | `complete`, listing the three expectations to confirm, the untestable promo route and the refund conflict note |
| Missing service URL | Claude Code | `claude-explicit-missing-url-l0kb85a8` | Passed: 10 cases | `blocked` (URL missing) | `blocked` |

All four recorded zero service requests, returned final responses, preserved the business
files, index and HEAD, and passed the timed order checks. In the 6574456 batch, both
extended runs had reported `partial` although no uncovered row remained; with the ordered
rule the Codex run now reports `blocked` for the same kind of remaining work. The manifest
and results are in `~/.cache/repo2test-host-probes/a93259a-status-EWbknKfL/`. All **48
host-probe unit tests passed**.
