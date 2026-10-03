import os
import subprocess

import pytest
import yaml

from apitest.workspace_cli import _write_ci


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("base_exists", [True, False])
def test_generated_ci_validates_candidate_against_available_base(tmp_path, provider, base_exists):
    _write_ci(tmp_path, provider, "@reviewers")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=t@invalid",
         "commit", "--allow-empty", "-qm", "base"], check=True,
    )
    base = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip() if base_exists else "0" * 40
    if provider == "github":
        ci = yaml.safe_load((tmp_path / ".github/workflows/validate.yml").read_text())
        step = ci["jobs"]["validate"]["steps"][-1]
        assert "merge_group.base_sha" in step["env"]["BASE"]
        assert "pull_request.base.sha" in step["env"]["BASE"]
        assert "github.event.before" in step["env"]["BASE"]
        script = step["run"]
    else:
        ci = yaml.safe_load((tmp_path / ".gitlab-ci.yml").read_text())
        script = ci["validate"]["script"][-1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CI_PROBE_ARGS"\n')
    uv.chmod(0o755)
    args_file = tmp_path / "invocation"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "CI_PROBE_ARGS": str(args_file), "BASE": base,
           "CI_MERGE_REQUEST_TARGET_BRANCH_SHA": base,
           "CI_MERGE_REQUEST_EVENT_TYPE": "merged_result"}
    result = subprocess.run(["bash", "-ec", script], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert args_file.read_text().splitlines() == [
        "run", "--locked", "apitest", "validate", *(["--base", base] if base_exists else [])
    ]
    if provider == "gitlab":
        args_file.unlink()
        env["CI_MERGE_REQUEST_EVENT_TYPE"] = "detached"
        result = subprocess.run(["bash", "-ec", script], cwd=tmp_path, env=env, capture_output=True)
        assert result.returncode == 2 and not args_file.exists()
