# Environment setup

Read the workspace's existing profiles and declared environment keys. Ask only for missing
information, starting with service URLs. `apitest workspace configure --service <name>
--url <url> --environment non-production` writes a profile reference and ignored local
environment value; use `production` when that is the actual category. Never infer
non-production merely from a hostname. A URL can be supplied while source scanning proceeds.
If the category is not known yet, configure the URL without `--environment`; keep writes
blocked until the user can identify the actual environment. URL setup does not require DB credentials.
The selected profile must declare its own environment; `extends` never inherits this
authorization from a parent profile. Classify each profile's actual service targets.

Service profiles support headers and named `roles`, each with its own headers. Reference
tokens/API keys with `${VARIABLE}`. A profile with a literal credential fails to load; the
error names the field and a variable for the ignored `.env` or CI environment. For a
documented ordinary HTTP login, extract the token
and use it only on subsequent appropriate-service/role requests. In a read-only case, write
the login as the first execute step, because a setup POST counts as a write. A write case
whose setup needs the token may log in during setup.
Captcha/SSO requires a user-provided usable test identity. Do not paste credentials into
cases or committed profiles. Use declared external test-data keys for accounts/fixtures:
list the key in `repo2test.toml` (`[test_data]`, `keys = ["admin"]`), give the profile
`test_data: {admin: {email: "${ADMIN_EMAIL}", password: "${ADMIN_PASSWORD}"}}`, and
reference it in a case as `${ctx.profile.test_data.admin.email}`.
Profile validation recognizes credentials only by field names and URL passwords; keep any
value the user marks sensitive external as well. Before committing, confirm that
`git ls-files .env` prints nothing and review the staged diff.

Only `<workspace>/.env` is loaded, and a variable already set in the process environment
takes precedence over it; selecting a profile does not load `.env.<profile>`. Before
switching targets, check for conflicting exported variables without printing their values.

Ask for DB/Redis/RabbitMQ configuration only for checks or helpers that actually need it.
`apitest preflight --profile <name>` reports missing prerequisites without calling services.
Missing required checks are blocked, not silently dropped. Keep source-derived plans when
execution is blocked and explain the precise missing inputs.

External dependencies use existing test services or sandboxes; use existing mocks only
when the user specifies them. Unavailable third-party paths or unrepeatable fault scenarios
remain explicit gaps. v1 does not automatically provision external systems.
