import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).parents[3] / ".github" / "workflows"
PRE_COMMIT = Path(__file__).parents[3] / ".pre-commit-config.yaml"


def _check_reaches_cargo() -> bool:
    """`make check` runs every pre-commit hook, so a cargo hook there compiles on a job whose own
    script never says `cargo`. Read the hooks rather than the job's text, or the cache rule stops
    at the word."""
    loaded = yaml.load(PRE_COMMIT.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return any(
        "cargo" in str(hook.get("entry", ""))
        for repo in loaded["repos"]
        for hook in repo.get("hooks", [])
    )


_CHECK_RUNS_CARGO = _check_reaches_cargo()


def test_runner_builds_restore_their_package_caches() -> None:
    for workflow in ["ci.yaml", "integration.yaml", "deploy.yml", "deploy-production.yml"]:
        loaded = yaml.load((WORKFLOWS / workflow).read_text(), Loader=yaml.BaseLoader)
        assert isinstance(loaded, dict)
        jobs = loaded["jobs"]
        assert isinstance(jobs, dict)

        for job_name, job in jobs.items():
            assert isinstance(job, dict)
            steps = job.get("steps", [])
            assert isinstance(steps, list)
            script = "\n".join(step.get("run", "") for step in steps if isinstance(step, dict))

            if "pnpm -C " in script:
                setup = [
                    step
                    for step in steps
                    if isinstance(step, dict)
                    and str(step.get("uses", "")).startswith("actions/setup-node@")
                ]
                assert len(setup) == 1, job_name
                assert setup[0]["with"]["cache"] == "pnpm", job_name
                roots = set(re.findall(r"pnpm -C ([^ ]+)", script))
                lockfiles = set(setup[0]["with"]["cache-dependency-path"].splitlines())
                assert lockfiles == {f"{root}/pnpm-lock.yaml" for root in roots}, job_name
                first_install = next(
                    index
                    for index, step in enumerate(steps)
                    if isinstance(step, dict) and "pnpm -C " in step.get("run", "")
                )
                assert steps.index(setup[0]) < first_install, job_name

            if "uv " in script:
                setup = [
                    step
                    for step in steps
                    if isinstance(step, dict)
                    and str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
                ]
                assert len(setup) == 1, job_name
                assert setup[0]["with"]["enable-cache"] == "true", job_name
                first_install = next(
                    index
                    for index, step in enumerate(steps)
                    if isinstance(step, dict) and "uv " in step.get("run", "")
                )
                assert steps.index(setup[0]) < first_install, job_name

            if "cargo " in script or ("make check" in script and _CHECK_RUNS_CARGO):
                cache = [
                    step
                    for step in steps
                    if isinstance(step, dict)
                    and str(step.get("uses", "")).startswith("Swatinem/rust-cache@")
                ]
                assert cache, job_name
                first_build = next(
                    index
                    for index, step in enumerate(steps)
                    if isinstance(step, dict)
                    and ("cargo " in step.get("run", "") or "make check" in step.get("run", ""))
                )
                assert all(steps.index(step) < first_build for step in cache), job_name

            for step in steps:
                if not isinstance(step, dict) or step.get("uses") != "docker/build-push-action@v6":
                    continue
                assert "cache-from" in step["with"], job_name
                assert "cache-to" in step["with"], job_name
