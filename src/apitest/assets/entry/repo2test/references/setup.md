# Workspace setup

Set up a workspace only for a business repository that serves an HTTP API. Carry out
steps 1 to 3 yourself, then ask the user once, in step 4. Nothing is created in or beside
the repository before they agree; the wheel goes to a temporary directory.

Setup needs network access for the wheel and Python packages, and write access to the
destination, its Git repository and uv's directories. When the host's sandbox blocks a
required command, first request approval to execute it with the needed permissions, then
retry and continue after approval. In Codex this can mean executing the command outside
the sandbox. Execute steps 5 to 7 only after the settings confirmation in step 4.

Codex's default `workspace-write` sandbox blocks network access. `--add-dir` permits
ordinary writes, but protected paths such as `.git` can remain read-only. On Linux, the
sandbox can mount placeholder directories in an otherwise empty destination: `init` then
reports a non-empty destination and `git init` fails on the read-only `.git`. This was
reproduced with Codex CLI 0.160.0. Request approval to retry outside the sandbox when these
placeholders cause the failure; preserve real existing files and the sandbox protections.

Only when the required approval is unavailable, explain the blocked operation and give
the exact recovery step. For network access in Codex, relaunch with
`-c sandbox_workspace_write.network_access=true`. For initialization writes, give the
commands of steps 5 to 7 after confirmation for the user to run in their terminal. Continue
from step 8 once they succeed. A sandbox failure is not a reason to ask for another wheel.

1. **Tools.** Check `python3 --version` (3.11 or later), `git --version` and
   `uv --version`. When uv is missing, offer to install it the way its documentation
   describes, and continue once the user agrees.
2. **Runner wheel.** Use the wheel the user names, such as `vendor/apitest-*.whl` of their
   team's test workspace or one they built in a framework checkout. Otherwise download the
   latest release, from this skill's directory:

   ```sh
   python3 scripts/fetch_wheel.py --dest <temporary-directory>
   ```

   The script reads only the official repository's latest release, checks the wheel against
   the SHA-256 the release publishes, and prints the wheel path, version and digest. If it
   stops for a reason other than the sandbox, report its message and ask the user for a
   wheel. Never download a wheel any other way. `init` copies the wheel into the
   workspace, so the temporary copy is not needed afterwards.
3. **Values.** Take each value from the user's request when it is there. Otherwise derive
   it as described; never invent one, and ask only where no default is given.
   - `--target`: the business repository path the locator reported.
   - `--ref`: the tag or commit the user names as deployed to the test environment. It must
     resolve in the local business checkout. If a named ref is missing there, ask the user
     to make it available; do not fetch in the business checkout or substitute another ref.
     If only the deployment time is known, use
     `git -C <business> rev-list --first-parent -1 --before=<time> <branch>`. If the user
     named neither, do not ask: use the commit at the head of the default branch in the
     local checkout, the branch `origin/HEAD` names or else the current one, and add
     `--estimated-because "<reason>"`.
   - `--runner-owner`: the maintainer the user names, written as `@user` or `@group/team`.
     Otherwise the current user's account on the Git host, written as `@<login>`, from an
     available platform tool such as `gh api user --jq .login`. Ask only when no tool
     reports one.
   - `--ci`: `github` or `gitlab`, from the host of the business repository's remote. Ask
     only when it has no remote or the host is neither.
   - The name and URL of each service under test. Name a service with a lowercase slug
     (`[a-z][a-z0-9_-]*`), such as the repository or module name. Use the URL the user
     gave. Otherwise propose the local address the repository's own configuration declares,
     `http://localhost:<port><context-path>` without a trailing slash; read it, never probe
     it. When the configuration does not say, leave the service unconfigured: cases can be
     written without a URL.
4. **Confirm once.** Show one summary and ask whether to go ahead: the workspace path, the
   wheel version and where it came from, the ref and whether it is an estimate, the runner
   owner, the CI, each service URL or "not configured", and that the workspace becomes a
   local Git repository of its own with one commit and no remote. When the destination
   lies inside another Git repository, say that the workspace will be nested in it. Say
   that the user can change any value or name an existing workspace instead, and apply
   what they change. For a workspace elsewhere, run the locator with `--start <its path>`.
5. **Initialize** the empty or absent destination, outside the business repository,
   normally the locator's `suggested` path:

   ```sh
   uvx --from <wheel> apitest workspace init <workspace> --target <business> \
     --ref <ref> --runner-wheel <wheel> --runner-owner <owner> --ci <github|gitlab>
   ```

   Append `--estimated-because "<reason>"` when the ref is an estimate.

   In a framework checkout, `uv run --locked apitest workspace init …` is equivalent.
   Resolving dependencies needs package-index access; when only the uv cache is available,
   pass `--offline` to both `uvx` and `init`. If `init` fails, it removes what it wrote, so
   fix the reported problem and rerun the same command.
6. **Prepare** in the new workspace: `uv sync --locked`, then
   `uv run --locked apitest workspace configure --service <name> --url <url>` for each
   confirmed URL, with `--profile <name>` when the user named a profile other than
   `local`, then `uv run --locked apitest workspace doctor`. Add
   `--environment non-production` only when the user states that the service is
   non-production; a local address does not show it.
7. **Make it a Git repository.** The workspace must be the root of its own repository, so
   run `git init` in it even when a directory above it is a Git repository. Then run
   `git add -A`, confirm that `git ls-files .env` prints nothing, review the staged files
   and commit the scaffold, so that later work shows as a diff. `init` never creates a
   remote; do not create one either.
8. Rerun `scripts/locate.py --start <workspace>` from this skill's directory and continue
   with the workspace's pinned skill. From the business repository the locator finds only
   the sibling `<repository>-e2e`, so when the workspace is anywhere else, tell the user to
   start later sessions in it or to name it.
