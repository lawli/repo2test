# Execution and results

Execute only the user-selected cases with the workspace's locked runner. Use
`uv run --locked apitest run --service <service> --profile <profile>` or repeatable `--id`.
Writes require explicit `environment: non-production`, isolated data and executable cleanup.
Do not relabel an environment to make blocked cases run.

Missing configuration blocks affected cases while others continue. A blocked selection is
incomplete with a nonzero exit. Business failure, transport/setup error, and cleanup failure
are separate information; retain the first business failure and report remaining cleanup
work. Cleanup errors always make the run unsuccessful and include redacted tracked data.

Correct expectations remain intact for known defects. `known_defects` records `ref`, execute
`step`, optional assertion `at` (status/json.path/headers.name/text/extract.name), and
optional `env`.
Use a tracker ID (`PROJ-123`, `#123`, `owner/repo#123`), an HTTP(S) issue URL, or an
existing `defects/*.md` record for `ref`.
Only matching business assertion failures are XFAIL; unexpected errors and other locations
remain failures. The one transport result a marker can name is `at: response`: the service
took the request and ended the connection without a response. Only a marker with exactly
that `at` on that step matches it; an unreachable service or a timeout stays an error.
A marked case passing unexpectedly is XPASS and fails the run. Blockers and
cleanup failures cannot be masked by defect markers. Intermittent failures belong in defect
records and `flaky` tags, not deterministic XFAIL markers.

Only an executed run writes `reports/summary.json`; a dry run or a command that fails before
execution leaves any earlier report in place. Check the exit status and the report's
modification time, and never present an unchanged report as this invocation's result.

Pending expectations still execute and report actual results, but neither they nor XFAIL
count as verified coverage. A pending case that fails is a `FAIL` labelled `pending
expectation not met` and counted in `totals.pending_fail`; it still fails the run. Use
`apitest coverage --results reports/summary.json` to attach observed coverage to the
recorded workspace version and environment; do not combine stale or incompatible results
into a claim of current coverage.
