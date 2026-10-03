# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through
[GitHub security advisories](https://github.com/lawli/repo2test/security/advisories/new).
Do not open a public issue. Include the runner version, the command or prompt, and a
minimal example with credentials and private business data removed.

## Supported versions

Fixes are released for the latest version only. Workspaces pin their runner, so apply a
fix with `apitest workspace upgrade`.

## What counts as a vulnerability

repo2test runs against live services with real credentials, so these are in scope:

- A mutating case, fixture or Python helper executing against a profile that is not
  `environment: non-production`.
- A credential supplied through `.env` or the process environment appearing unredacted in
  a report, a recorded exchange, an error message or `apitest profile show`.
- A literal credential in a recognized profile field loading, or passing
  `apitest validate`.
- `verify.db.sql` executing anything other than a read-only `SELECT`.
- `apitest workspace doctor` passing when the vendored wheel or a pinned skill or entry
  asset differs from its recorded bytes.
- `apitest sweep` deleting rows other than the exact IDs in its manifest.

## Known limits

- Credential and secret checks are pattern-based. They do not prove that a file is free
  of sensitive data; review staged changes before committing.
- The skills instruct the agent: keep the business repository read-only, call services
  only when asked to run, never create a remote. The runner enforces only the checks
  listed above. Host permissions and sandboxing remain your responsibility.
- Python helpers are code from the test workspace that runs with your credentials.
  Review them like any other code.
