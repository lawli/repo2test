# Workspace setup

Initialize only after the user chooses it, and only for a business repository that serves an
HTTP API.

1. **Runner wheel.** Use a trusted repo2test wheel: `dist/apitest-*.whl` built with
   `uv build --wheel` in a framework checkout, or `vendor/apitest-*.whl` from an existing
   team test workspace. If neither is available, ask the user for one; never download an
   unknown wheel.
2. **Inputs.** Take each value from the user or the repository; never invent one.
   - `--target`: the business repository path, inside a Git repository.
   - `--ref`: the tag or commit deployed to the test environment. It must resolve in the
     local business checkout. If a known ref is missing there, ask the user to make it
     available; do not fetch in the business checkout or substitute another ref. If only
     the deployment time is known, use
     `git -C <business> rev-list --first-parent -1 --before=<time> <branch>`. If neither is
     known, use the default branch head and add `--estimated-because "<reason>"`.
   - `--runner-owner`: ask for the runner maintainer, written as `@user` or `@group/team`.
   - `--ci`: `github` or `gitlab`, where the test repository will be hosted; ask if unknown.
3. **Initialize** the empty or absent destination, outside the business repository,
   normally the locator's `suggested` path:

   ```sh
   uvx --from <wheel> apitest workspace init <workspace> --target <business> \
     --ref <ref> --runner-wheel <wheel> --runner-owner <owner> --ci <github|gitlab>
   ```

   In a framework checkout, `uv run --locked apitest workspace init …` is equivalent.
   Resolving dependencies needs package-index access; when only the uv cache is available,
   pass `--offline` to both `uvx` and `init`. If `init` fails, it removes what it wrote, so
   fix the reported problem and rerun the same command. It never creates a remote; do not
   create one either.
4. **Prepare** in the new workspace: `uv sync --locked`, then
   `uv run --locked apitest workspace configure --service <name> --url <url>` for each
   service under test, then `uv run --locked apitest workspace doctor`. Add
   `--environment non-production` only when the user states that the service is
   non-production.
5. Rerun `scripts/locate.py --start <business>` from this skill's directory and continue
   with the workspace's pinned skill.
