# Case review criteria

Use these with the workspace's pinned authoring skill. Assertion quality follows the
contract and observable behavior; it has no fixed response-field count.

1. Every discovered endpoint retains an inventory fragment, evidence and at least one
   coverage/gap row. Route registration reconciliation exposes omissions. Unknown dynamic
   routes and unavailable configuration remain explicit discovery gaps.
2. Each case has a stable identity and unambiguous `(coverage key, variant)`, tied to the
   analyzed reference commit. Requirements/contracts or declarative evidence justify
   confirmed expectations; imperative implementation alone leaves them pending.
3. Happy paths and meaningful edge cases assert the business outcome. Prefer API-visible
   effects; declare DB/Redis/MQ checks only when necessary, including helper dependencies.
4. Writes use a confirmed non-production environment, isolated test-owned data and
   executable cleanup. Failure paths must leave traceable recovery information.
5. Preserve human edits and review source drift. Modified cases/rows use the current
   reference anchor. Only byte-exact replay of deterministic migration in a runner upgrade
   qualifies for retaining an old case anchor.
6. Keep correct expectations for known defects. Scope a strict marker to an execute step
   and optionally assertion location/profile; do not assert a broken envelope to make it pass.
7. Report generation and execution separately. `PASS` with confirmed expectations may
   contribute verified coverage for that exact workspace and source version. Pending,
   `BLOCKED`, `XFAIL`, `XPASS`, assertion errors and cleanup errors never count as verified.

No scenario is fully covered while relevant rows are uncovered, blocked, pending, gaps or
awaiting review. Generated views are derived from endpoint fragments and case-local markers,
not maintained as a shared handwritten matrix.
