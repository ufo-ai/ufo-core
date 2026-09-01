import dataclasses
import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import yaml

from evals.__main__ import UFO_APP_BENCH_BACKENDS
from evals.memory_ingestion.assets import LOCOMO, LONGMEM_CLEANED
from evals.memory_ingestion.materialize import (
    DERIVATION_MODEL,
    DerivedEvidence,
    IngestionReadiness,
)
from evals.registry import TASKS
from evals.stack import (
    APP_PAGE_SUITES,
    APP_SUITES,
    APPLICATION_BUILD_PRODUCTS,
    CREATION_SUITES,
    DOCKER_BACKEND,
    Matrix,
)
from sandbox.build_template import SANDBOX_CLIENT_TARGET
from ufo.config import Config
from ufo.harness.sandbox.client_binary import CLIENT_BINARY_NAME
from ufo.schema.records import DEFAULT_REASONING_EFFORT

ROOT = Path(__file__).parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "evals-nightly.yml"
SCRIPTS = ROOT / ".github" / "scripts"
# GitHub stops any job on a hosted runner at six hours, whatever `timeout-minutes` names.
RUNNER_CEILING_MINUTES = 360


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def planner():
    return _script("nightly_eval_matrix")


@pytest.fixture(scope="module")
def memory_nightly():
    return _script("nightly_memory_ingestion")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_sweep_plans_every_registered_suite_exactly_once(planner) -> None:
    """The sweep claims to run every registered suite. A hand-kept list of names in the workflow
    dropped one silently; the plan reads the registry, so a new or renamed suite cannot fall out."""
    planned = [suite for shard in planner.plan(smoke=False) for suite in shard.suites]

    assert sorted(planned) == sorted(task.name for task in TASKS)
    assert len(planned) == len(set(planned))


def test_every_shard_runs_its_suites_under_a_pack_that_accepts_them(planner) -> None:
    bound = {task.name: task.packs for task in TASKS if task.packs}

    for shard in planner.plan(smoke=False):
        for suite in shard.suites:
            assert shard.pack in bound.get(suite, (shard.pack,)), (shard.label, suite)


def test_an_agent_specific_suite_runs_in_its_own_shard(planner) -> None:
    shard = next(shard for shard in planner.plan(smoke=False) if "code_review" in shard.suites)

    assert shard.agent == "code"
    assert shard.suites == ("code_review",)


def test_the_chat_home_suite_runs_in_the_chat_agent_context(planner) -> None:
    shard = next(shard for shard in planner.plan(smoke=False) if "app_home_change" in shard.suites)

    assert shard.agent == "chat"
    assert shard.suites == ("app_home_change",)


def test_agent_specific_shard_labels_are_artifact_safe(planner) -> None:
    profile = next(shard for shard in planner.plan(smoke=False) if "coding_profile" in shard.suites)

    assert profile.agent == "profile:coding"
    assert profile.label.startswith("nightly-assistant-eval-profile-coding")
    assert all(
        planner.UNSAFE_LABEL_CHARS.search(shard.label) is None
        for shard in planner.plan(smoke=False)
    )


def test_application_suites_do_not_share_a_shard_with_other_suites(planner) -> None:
    for shard in planner.plan(smoke=False):
        app_suites = planner.APP_SUITES.intersection(shard.suites)
        assert not app_suites or set(shard.suites) <= planner.APP_SUITES


def test_creation_suites_do_not_share_a_shard_with_other_suites(planner) -> None:
    for shard in planner.plan(smoke=False):
        creation_suites = planner.CREATION_SUITES.intersection(shard.suites)
        assert not creation_suites or set(shard.suites) <= planner.CREATION_SUITES


def test_a_suite_bound_to_no_planned_pack_fails_the_plan(planner, monkeypatch) -> None:
    stranded = tuple(
        dataclasses.replace(task, packs=("no_such_pack",)) if task.name == "basics" else task
        for task in TASKS
    )
    monkeypatch.setattr(planner, "TASKS", stranded)

    with pytest.raises(SystemExit, match="no sweep arm runs"):
        planner.plan(smoke=False)


def test_no_shard_carries_more_than_one_job_can_finish(planner, workflow) -> None:
    """A shard's weight is its serial case slots. The sweep overran the six-hour runner ceiling as
    one job; the split only holds while every shard stays a fraction of that."""
    weights = {task.name: planner._weight(task) for task in TASKS}
    sweep = workflow["jobs"]["sweep"]
    deadline = next(step for step in sweep["steps"] if step.get("name") == "Run the shard")

    for shard in planner.plan(smoke=False):
        assert sum(weights[suite] for suite in shard.suites) <= planner.SHARD_WEIGHT, shard.label
    assert deadline["timeout-minutes"] < sweep["timeout-minutes"] < RUNNER_CEILING_MINUTES


def test_the_smoke_subset_boots_every_arm_it_can_reach(planner) -> None:
    """The proving run exists to catch a shard whose pack knobs do not boot a serve, so every arm
    carries a case — the hosted arm's browser and index knobs are named nowhere else."""
    shards = planner.plan(smoke=True)

    assert {shard.pack for shard in shards} == {arm.pack for arm in planner.ARMS}
    assert all(planner.SMOKE_PROBE in shard.suites for shard in shards if shard.agent is None)
    assert any("ufo-app-qa-replay" in shard.suites for shard in shards)
    assert sum(len(shard.suites) for shard in shards) < len(TASKS)


def test_the_nightly_models_have_artifact_safe_labels(planner, capsys) -> None:
    assert [model.id for model in planner.NIGHTLY_MODELS] == [
        "claude-opus-5",
        "z-ai/glm-5.3-flash",
    ]
    assert all(
        planner.UNSAFE_LABEL_CHARS.search(model.label) is None for model in planner.NIGHTLY_MODELS
    )
    assert [model.reasoning for model in planner.NIGHTLY_MODELS] == ["auto", "medium"]
    planner.main(["--models"])
    assert json.loads(capsys.readouterr().out) == [
        {"id": model.id, "label": model.label} for model in planner.NIGHTLY_MODELS
    ]


def test_fixed_model_shards_run_once_while_auto_model_shards_run_twice(planner, capsys) -> None:
    jobs = planner.sweep_jobs(smoke=False)
    for shard in planner.plan(smoke=False):
        carried = tuple(job for job in jobs if job.shard == shard)
        fixed_model = not planner.APP_SUITES.isdisjoint(shard.suites) or (
            shard.agent is not None and shard.agent.startswith("profile:")
        )

        assert len(carried) == (1 if fixed_model else len(planner.NIGHTLY_MODELS))
        assert all((job.expected_model is None) == fixed_model for job in carried)

    planner.main(["--jobs", "--smoke"])
    payload = json.loads(capsys.readouterr().out)
    fixed = next(job for job in payload if job["target"] == "fixed model")
    assert fixed["artifact"] == planner.FIXED_MODEL_LABEL
    assert fixed["model"] == planner.NIGHTLY_MODELS[0].id
    assert {job["reasoning"] for job in payload if job["target"] == "z-ai/glm-5.3-flash"} == {
        "medium"
    }
    assert {job["reasoning"] for job in payload if job["target"] == "claude-opus-5"} == {"auto"}

    planner.main(["--jobs", "--smoke", "--reasoning", "low"])
    assert {job["reasoning"] for job in json.loads(capsys.readouterr().out)} == {"low"}


def test_a_written_shard_is_input_the_stack_accepts(planner, tmp_path: Path) -> None:
    for shard in (*planner.plan(smoke=False), *planner.plan(smoke=True)):
        directory = tmp_path / shard.label
        planner.write(shard, directory, "z-ai/glm-5.3", "auto")
        matrix = Matrix.model_validate(tomllib.loads((directory / "matrix.toml").read_text()))
        config = tomllib.loads((directory / "ufo.toml").read_text())

        assert config["pack"]["name"] == shard.pack
        assert config["models"]["auto_model"] == "z-ai/glm-5.3"
        assert [spec.label for spec in matrix.run] == [shard.label]
        assert matrix.run[0].member_model_provider == "anthropic"
        assert matrix.run[0].reasoning is None
        assert matrix.run[0].args[-len(shard.suites) :] == shard.suites
        if shard.agent is not None:
            assert matrix.run[0].args[:4] == (
                "--concurrency",
                str(planner.CONCURRENCY),
                "--agent",
                shard.agent,
            )


def test_a_written_smoke_shard_accepts_a_reasoning_override(planner, tmp_path: Path) -> None:
    shard = planner.plan(smoke=True)[0]

    planner.write(shard, tmp_path, "z-ai/glm-5.3-flash", "low")
    matrix = Matrix.model_validate(tomllib.loads((tmp_path / "matrix.toml").read_text()))

    assert matrix.run[0].reasoning == "low"


def test_an_application_shard_keeps_its_profile_reasoning(planner, tmp_path: Path) -> None:
    shard = next(
        shard
        for shard in planner.plan(smoke=False)
        if not planner.APP_SUITES.isdisjoint(shard.suites)
    )

    planner.write(shard, tmp_path, "z-ai/glm-5.3-flash", "low")
    matrix = Matrix.model_validate(tomllib.loads((tmp_path / "matrix.toml").read_text()))

    assert matrix.run[0].reasoning is None


def test_the_workflow_fans_out_over_the_planned_shards(workflow) -> None:
    sweep = workflow["jobs"]["sweep"]
    plan = workflow["jobs"]["plan"]

    assert sweep["needs"] == ["plan", "sandbox-client", "web-build"]
    assert sweep["strategy"]["fail-fast"] is False
    assert sweep["strategy"]["matrix"] == {"include": "${{ fromJSON(needs.plan.outputs.jobs) }}"}
    assert '--jobs "$SWEEP_SMOKE"' in plan["steps"][-1]["run"]
    assert '--reasoning "$EVAL_REASONING"' in plan["steps"][-1]["run"]
    assert "--models" in plan["steps"][-1]["run"]


def test_the_nightly_models_and_dispatch_reasoning_reach_the_sweep(workflow) -> None:
    trigger = workflow.get("on", workflow[True])
    write = next(
        step
        for step in workflow["jobs"]["sweep"]["steps"]
        if step.get("name") == "Write the shard's stack input"
    )

    assert "model" not in trigger["workflow_dispatch"]["inputs"]
    assert trigger["workflow_dispatch"]["inputs"]["reasoning"] == {
        "description": "Evaluated agent reasoning effort",
        "type": "choice",
        "options": ["auto", "off", "low", "medium", "high"],
        "default": "auto",
    }
    assert workflow["env"]["EVAL_REASONING"] == "${{ inputs.reasoning || 'auto' }}"
    assert workflow["jobs"]["sweep"]["env"]["EVAL_MODEL"] == "${{ matrix.model }}"
    assert workflow["jobs"]["sweep"]["env"]["EVAL_REASONING"] == "${{ matrix.reasoning }}"
    assert '--model "$EVAL_MODEL"' in write["run"]
    assert '--reasoning "$EVAL_REASONING"' in write["run"]
    assert workflow["jobs"]["sweep"]["env"]["OPENROUTER_API_KEY"] == (
        "${{ secrets.OPENROUTER_API_KEY }}"
    )
    assert workflow["jobs"]["sweep"]["env"]["AWS_BEARER_TOKEN_BEDROCK"] == (
        "${{ secrets.AWS_BEARER_TOKEN_BEDROCK }}"
    )


def test_required_eval_credentials_fail_before_the_sweep_starts(workflow) -> None:
    credentials = workflow["jobs"]["credentials"]
    required = {
        "ANTHROPIC_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
        "DD_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "PERPLEXITY_API_KEY",
        "TURBOPUFFER_API_KEY",
    }
    check = credentials["steps"][0]["run"]

    assert set(credentials["env"]) == required
    assert all(f"secrets.{name}" in credentials["env"][name] for name in required)
    assert all(name in check for name in required)
    assert workflow["jobs"]["sandbox-client"]["needs"] == "credentials"
    assert workflow["jobs"]["web-build"]["needs"] == "credentials"
    assert workflow["jobs"]["memory-ingestion"]["needs"] == ["credentials", "plan"]


def test_every_sweep_shard_receives_one_shared_sandbox_client(workflow) -> None:
    producer = workflow["jobs"]["sandbox-client"]
    build = next(step for step in producer["steps"] if "cargo build" in step.get("run", ""))
    upload = next(
        step for step in producer["steps"] if step.get("uses", "").startswith("actions/upload")
    )
    staged = Path("client/target") / SANDBOX_CLIENT_TARGET / "release"

    assert upload["with"] == {
        "name": "sandbox-client",
        "path": str(staged / CLIENT_BINARY_NAME),
        "if-no-files-found": "error",
        "retention-days": 1,
    }
    assert f"--target {SANDBOX_CLIENT_TARGET}" in build["run"]
    sweep = workflow["jobs"]["sweep"]
    download = next(
        step
        for step in sweep["steps"]
        if step.get("uses", "").startswith("actions/download-artifact")
        and step["with"].get("name") == "sandbox-client"
    )
    install = sweep["steps"][sweep["steps"].index(download) + 1]

    assert "sandbox-client" in sweep["needs"]
    assert Path(download["with"]["path"]) == staged
    assert f"chmod +x {staged / CLIENT_BINARY_NAME}" in install["run"]
    assert f'{staged}" >> "$GITHUB_PATH"' in install["run"]
    memory = workflow["jobs"]["memory-ingestion"]
    assert memory["needs"] == ["credentials", "plan"]
    assert memory["env"]["OPENROUTER_API_KEY"] == "${{ secrets.OPENROUTER_API_KEY }}"
    assert memory["env"]["AWS_BEARER_TOKEN_BEDROCK"] == ("${{ secrets.AWS_BEARER_TOKEN_BEDROCK }}")


def test_an_app_page_shard_runs_its_suites_on_the_docker_sandbox_backend(
    planner, tmp_path: Path
) -> None:
    for shard in planner.plan(smoke=False):
        directory = tmp_path / shard.label
        planner.write(shard, directory, "claude-opus-5", DEFAULT_REASONING_EFFORT)
        config = tomllib.loads((directory / "ufo.toml").read_text())
        sandbox = config.get("sandbox", {})

        if APP_PAGE_SUITES.isdisjoint(shard.suites):
            assert "sandbox" not in config, shard.label
        else:
            backend = Config.model_validate(config).sandbox.backend
            assert sandbox == {"backend": DOCKER_BACKEND}, shard.label
            if not APP_SUITES.isdisjoint(shard.suites):
                assert backend in UFO_APP_BENCH_BACKENDS


def test_every_suite_that_builds_a_page_runs_on_docker(planner, tmp_path: Path) -> None:
    """A graded page build runs `vite build` inside the sandbox, and only the eval sandbox image
    carries vite — the local carrier takes it from the host PATH, where the sweep installs none.

    The names are the claim: each of these suites fails without a real build. `app_home_change`
    grades `dist/` under the workspace and refuses a hand-run build (evals/suites/app_home_change.py
    248, 335), and `new_application` grades a `deployed` build status
    (evals/suites/new_application.py:1033). Reading the expectation off `APP_PAGE_SUITES` instead
    would pass whatever that set happened to hold.
    """
    builders = {
        "ufo-app-bench",
        "ufo-app-copy",
        "ufo-app-qa-replay",
        "new_application",
        "app_home_change",
    }
    shards = planner.plan(smoke=False)
    assert builders <= {suite for shard in shards for suite in shard.suites}

    for shard in shards:
        if builders.isdisjoint(shard.suites):
            continue
        directory = tmp_path / shard.label
        planner.write(shard, directory, "claude-opus-5", DEFAULT_REASONING_EFFORT)
        config = tomllib.loads((directory / "ufo.toml").read_text())

        assert config.get("sandbox") == {"backend": DOCKER_BACKEND}, shard.label


def test_every_sweep_shard_receives_the_built_page_kit(planner, workflow) -> None:
    """The kit is build output no checkout carries, and `evals.stack` refuses an app or creation
    suite without it, so the sweep builds one and hands it to every shard."""
    gated = APP_SUITES | CREATION_SUITES
    assert any(gated.intersection(shard.suites) for shard in planner.plan(smoke=False))

    producer = workflow["jobs"]["web-build"]
    build = next(step for step in producer["steps"] if "run build" in step.get("run", ""))
    upload = next(
        step for step in producer["steps"] if step.get("uses", "").startswith("actions/upload")
    )

    assert build["run"].strip() == "pnpm -C extensions/web/frontend run build"
    assert upload["with"] == {
        "name": "sites-page-kit",
        "path": "extensions/sites/ufo_ext_sites/page/kit",
        "if-no-files-found": "error",
        "retention-days": 1,
    }
    sweep = workflow["jobs"]["sweep"]
    download = next(
        step
        for step in sweep["steps"]
        if step.get("uses", "").startswith("actions/download-artifact")
        and step["with"].get("name") == "sites-page-kit"
    )

    assert "web-build" in sweep["needs"]
    assert all(
        Path(download["with"]["path"]) == product.parent for product in APPLICATION_BUILD_PRODUCTS
    )


def test_every_shard_archives_its_own_records_and_the_archive_merges_them(workflow) -> None:
    """One job per shard means one artifact name per shard; a shard that fails still uploads what
    it recorded, and the archive is the only place the night reads as one result."""
    uploads = [
        step
        for step in workflow["jobs"]["sweep"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact")
    ]
    archive = workflow["jobs"]["archive"]
    download = next(
        step for step in archive["steps"] if step.get("uses", "").startswith("actions/download")
    )

    assert [step["with"]["name"] for step in uploads] == [
        "eval-run-records-${{ matrix.label }}-${{ matrix.artifact }}",
        "eval-stack-logs-${{ matrix.label }}-${{ matrix.artifact }}",
    ]
    assert uploads[0]["if"] == "always()" and uploads[1]["if"] == "failure()"
    assert archive["if"] == "always()" and archive["needs"] == ["sweep", "memory-ingestion"]
    assert download["with"]["pattern"] == "eval-run-records-*"
    assert download["with"]["merge-multiple"] is True


def test_the_summary_names_a_planned_suite_that_produced_no_report(planner, tmp_path: Path) -> None:
    """A shard that dies records nothing, and a green archive job would then report a coverage the
    night does not have. The summary reads the plan, so the gap is named where a reader looks."""
    summary = _script("eval_sweep_summary")
    (tmp_path / "runs").mkdir()

    rendered = summary.render(tmp_path, smoke=True)

    assert "0 of 0 planned suites ran" not in rendered
    assert "planned suite runs produced no report" in rendered
    assert all(suite in rendered for suite in planner.SMOKE_SUITES)


def test_only_a_complete_fixed_case_cohort_is_comparable(planner, tmp_path: Path) -> None:
    summary = _script("eval_sweep_summary")
    tasks = {task.name: task for task in TASKS}
    for job in planner.sweep_jobs(smoke=True):
        for suite in job.shard.suites:
            _archive(
                tmp_path,
                job.shard.label,
                suite,
                passed=len(tasks[suite].cases),
                scored=len(tasks[suite].cases),
                digest=f"sha256:{suite}",
                target_model=job.expected_model or "google/gemini-3.7-flash",
            )

    summary.require_comparable(tmp_path, smoke=True)

    record = next((tmp_path / "runs").glob("*.json"))
    payload = json.loads(record.read_text())
    payload["reports"][0]["cases"][0]["excluded"] = True
    record.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="unexpected exclusions"):
        summary.require_comparable(tmp_path, smoke=True)


def test_one_model_cannot_satisfy_a_comparable_cohort(planner, tmp_path: Path) -> None:
    summary = _script("eval_sweep_summary")
    tasks = {task.name: task for task in TASKS}
    omitted = planner.NIGHTLY_MODELS[1]
    for job in planner.sweep_jobs(smoke=True):
        if job.expected_model == omitted.id:
            continue
        for suite in job.shard.suites:
            _archive(
                tmp_path,
                job.shard.label,
                suite,
                passed=len(tasks[suite].cases),
                scored=len(tasks[suite].cases),
                digest=f"sha256:{suite}",
                target_model=job.expected_model or "google/gemini-3.7-flash",
            )

    with pytest.raises(RuntimeError, match=r"z-ai/glm-5\.3-flash"):
        summary.require_comparable(tmp_path, smoke=True)


def test_the_summary_requires_every_memory_ingestion_report(memory_nightly, tmp_path: Path) -> None:
    summary = _script("eval_sweep_summary")
    (tmp_path / "runs").mkdir()

    rendered = summary.render(tmp_path, smoke=True, memory_ingestion=True)

    assert all(name in rendered for name in memory_nightly.SMOKE_REPORT_CASES)


def test_memory_ingestion_inputs_pin_luna_and_the_corpus(memory_nightly, tmp_path: Path) -> None:
    root = tmp_path / "input"
    snapshot = tmp_path / "snapshot"

    memory_nightly.write_inputs(root, snapshot, "z-ai/glm-5.3-flash", "medium")

    config = tomllib.loads((root / "ufo.toml").read_text())
    matrix = Matrix.model_validate(tomllib.loads((root / "matrix.toml").read_text()))
    assert config["models"]["auto_model"] == "z-ai/glm-5.3-flash"
    assert config["models"]["background_jobs_model"] == DERIVATION_MODEL
    assert config["pack"]["name"] == "assistant"
    assert matrix.run[0].label == "memory-ingestion"
    assert matrix.run[0].memory_ingestion == snapshot.resolve()
    assert matrix.run[0].reasoning == "medium"


def test_memory_ingestion_smoke_uses_supported_cases(memory_nightly) -> None:
    """The gate carries only cases every model passes at least one of three samples. conv-30/060
    ("happy" vs a joy paraphrase) and conv-49/002 (answers Jasper, never the Rockies) went 0/3 on
    claude-opus-5, and longmem/9aaed6a3 (a "last Thursday" the answer anchors to the wrong week)
    went 0/3 on glm-5.3-flash — all with configuration, judge, and prompts pinned. Chronic misses
    read in the full sweep's aggregate, never as a gate."""
    assert "locomo/conv-26/120" in memory_nightly.SMOKE_CASES
    assert "longmem/a96c20ee" in memory_nightly.SMOKE_CASES
    assert "locomo/conv-42/037" in memory_nightly.SMOKE_CASES
    assert memory_nightly.SMOKE_SAMPLES == 3
    for excluded in (
        "locomo/conv-30/060",
        "locomo/conv-49/002",
        "longmem/9aaed6a3",
        "locomo/conv-41/134",
        "locomo/conv-26/146",
        "locomo/conv-30/030",
        "locomo/conv-41/006",
    ):
        assert excluded not in memory_nightly.SMOKE_CASES


def test_memory_ingestion_is_a_complete_independent_job(workflow, memory_nightly) -> None:
    job = workflow["jobs"]["memory-ingestion"]
    fetch = next(step for step in job["steps"] if step.get("name") == "Fetch the pinned corpora")
    prepare = next(
        step for step in job["steps"] if step.get("name") == "Prepare the memory-ingestion stack"
    )
    run = next(step for step in job["steps"] if step.get("name") == "Run memory ingestion")
    uploads = [
        step["with"]["name"]
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact")
    ]

    assert run["timeout-minutes"] < job["timeout-minutes"] < RUNNER_CEILING_MINUTES
    assert job["strategy"] == {
        "fail-fast": False,
        "matrix": {"model": "${{ fromJSON(needs.plan.outputs.models) }}"},
    }
    assert job["env"]["EVAL_MODEL"] == "${{ matrix.model.id }}"
    assert fetch["env"]["LONGMEM_URL"] == LONGMEM_CLEANED.url
    assert fetch["env"]["LOCOMO_URL"] == LOCOMO.url
    assert '"$SWEEP_SMOKE"' in prepare["run"] and '"$SWEEP_SMOKE"' in run["run"]
    assert '--model "$EVAL_MODEL"' in prepare["run"]
    assert '--reasoning "$EVAL_REASONING"' in prepare["run"]
    assert '--model "$EVAL_MODEL"' in run["run"]
    assert "nightly_memory_ingestion.py verify" in run["run"]
    assert uploads == [
        "eval-run-records-memory-ingestion-${{ matrix.model.label }}",
        "eval-memory-ingestion-state-${{ matrix.model.label }}",
        "eval-stack-logs-memory-ingestion-${{ matrix.model.label }}",
    ]
    assert "memory-ingestion-state/$MODEL_LABEL/readiness.json" in run["run"]
    state_upload = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact")
        and step["with"]["name"].startswith("eval-memory-ingestion-state-")
    )
    assert state_upload["with"]["path"] == "memory-ingestion-state"


def test_a_failing_memory_ingestion_case_ends_the_step_red(workflow) -> None:
    """`set +e` is there so `verify` still writes the state and the record over a failed stack, and
    `verify` compares the record's shape and never reads `case.passed`. The stack's own code is the
    only thing left that can turn the night red, so the step ends on it."""
    run = next(
        step
        for step in workflow["jobs"]["memory-ingestion"]["steps"]
        if step.get("name") == "Run memory ingestion"
    )

    assert "stack_status=$?" in run["run"]
    assert run["run"].rstrip().endswith('exit "$stack_status"')


def test_memory_ingestion_verification_accepts_scores_and_rejects_missing_cases(
    memory_nightly, tmp_path: Path
) -> None:
    reports = tmp_path / "reports"
    runs_root = tmp_path / "runs"
    report_rows = []
    for name, count in memory_nightly.SMOKE_REPORT_CASES.items():
        report_rows.append(
            {
                "name": name,
                "suite": "capability",
                "digest": f"sha256:{name}",
                "target_model": "z-ai/glm-5.3",
                "cases": [
                    {
                        "name": f"{name}-{index}",
                        "passed": index % 2 == 0,
                        "reason": "score",
                        "evidence": {"prompt": "p", "response": "r"},
                    }
                    for index in range(count)
                ],
            }
        )
    run = {
        "id": str(uuid4()),
        "created_at": "2026-08-19T05:00:00Z",
        "label": "memory-ingestion",
        "agent": "assistant",
        "ufo_version": "0",
        "revision": "0",
        "reports": report_rows,
    }
    (reports / "runs").mkdir(parents=True)
    (reports / "runs" / "run.json").write_text(json.dumps(run))
    readiness = IngestionReadiness(
        snapshot_digest="snapshot",
        corpus_digest="corpus",
        derivation_model=DERIVATION_MODEL,
        workspace_id=uuid4(),
        source_id=uuid4(),
        pages_root=tmp_path / "pages",
        page_count=9,
        memory_count=41,
        chunk_count=41,
        asker_email="asker@example.com",
        evidence=(),
    )
    state = runs_root / "stamp" / "memory-ingestion" / "state" / "digest" / "readiness.json"
    state.parent.mkdir(parents=True)
    state.write_text(readiness.model_dump_json())
    output = tmp_path / "state" / "readiness.json"

    memory_nightly.verify(reports, runs_root, output, smoke=True, model="z-ai/glm-5.3")
    assert output.read_text() == state.read_text()

    report_rows[0]["target_model"] = "claude-opus-5"
    (reports / "runs" / "run.json").write_text(json.dumps(run))
    with pytest.raises(RuntimeError, match="memory ingestion recall used"):
        memory_nightly.verify(reports, runs_root, output, smoke=True, model="z-ai/glm-5.3")
    report_rows[0]["target_model"] = "z-ai/glm-5.3"

    report_rows[0]["cases"].pop()
    (reports / "runs" / "run.json").write_text(json.dumps(run))
    with pytest.raises(RuntimeError, match="report cases differ"):
        memory_nightly.verify(reports, runs_root, output, smoke=True, model="z-ai/glm-5.3")


def test_the_archive_survives_a_memory_ingestion_job_that_wrote_no_state(workflow) -> None:
    """The archive job runs `if: always()` so a failed sibling still leaves the night readable. The
    ingestion job publishes no state artifact when it ends before `verify`, and a download by name
    would stop the job there — no summary, no archive, no S3 copy, no trend point."""
    metrics = _script("eval_sweep_metrics")
    downloads = [
        step
        for step in workflow["jobs"]["archive"]["steps"]
        if step.get("uses", "").startswith("actions/download-artifact")
    ]
    state = next(step for step in downloads if "memory-ingestion" in step["with"]["pattern"])

    assert all("name" not in step["with"] for step in downloads)
    assert state["with"]["pattern"] == "eval-memory-ingestion-state-*"
    assert state["with"]["merge-multiple"] is True
    assert state["with"]["path"] == str(Path("eval-reports") / metrics.MEMORY_STATE_ROOT)


def test_the_archive_carries_the_summary_the_viewer_and_the_records(workflow) -> None:
    """A record is megabytes of case evidence. The night is read from `summary.md` and opened as
    `index.html`, so both are written into the archive the artifact and S3 both take whole."""
    steps = workflow["jobs"]["archive"]["steps"]
    summarize = next(step for step in steps if step.get("name") == "Summarize the sweep")
    upload = next(step for step in steps if step.get("name") == "Upload the sweep archive")
    s3 = next(step for step in steps if step.get("name") == "Archive the sweep")

    assert "tee eval-reports/summary.md" in summarize["run"]
    assert upload["with"]["path"] == "eval-reports"
    assert "eval-reports" in s3["run"] and "--recursive" in s3["run"]


def _archive(
    root: Path,
    label: str,
    name: str,
    passed: int,
    scored: int,
    digest: str,
    target_model: str = "claude-opus-5",
) -> None:
    cases = [
        {
            "name": f"{name}-{index}",
            "passed": index < passed,
            "reason": "",
            "evidence": {"prompt": "p", "response": "r"},
        }
        for index in range(scored)
    ]
    run = {
        "id": str(uuid5(NAMESPACE_URL, f"{label}/{name}/{target_model}")),
        "created_at": "2026-08-19T05:00:00Z",
        "label": label,
        "agent": "assistant",
        "ufo_version": "0",
        "revision": "0",
        "reports": [
            {
                "name": name,
                "suite": "capability",
                "digest": digest,
                "target_model": target_model,
                "cases": cases,
            }
        ],
    }
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "runs" / f"{run['id']}.json").write_text(json.dumps(run))


def test_the_trend_submits_counts_per_suite_never_a_rate(tmp_path: Path) -> None:
    """A rate per suite cannot be re-aggregated: averaging 40 of them weights a one-case suite like
    a sixty-nine-case one. The sweep submits what it counted and the query divides."""
    metrics = _script("eval_sweep_metrics")
    _archive(tmp_path, "shard-a", "basics", passed=2, scored=3, digest="sha256:aa")

    payload = metrics.series(tmp_path, mode="sweep", timestamp=1787000000)
    by_metric = {point["metric"]: point for point in payload["series"]}

    assert set(by_metric) == {metrics.PASSED_METRIC, metrics.SCORED_METRIC}
    assert by_metric[metrics.PASSED_METRIC]["points"][0]["value"] == 2
    assert by_metric[metrics.SCORED_METRIC]["points"][0]["value"] == 3
    assert not any("rate" in point["metric"] for point in payload["series"])


def test_the_summary_names_the_target_model(tmp_path: Path) -> None:
    summary = _script("eval_sweep_summary")
    _archive(
        tmp_path,
        "shard-a",
        "basics",
        passed=1,
        scored=1,
        digest="sha256:aa",
        target_model="z-ai/glm-5.3-flash",
    )

    rendered = summary.render(tmp_path, smoke=True)

    assert "| Suite | Shard | Target Model | Passed | Rate |" in rendered
    assert "| basics | shard-a | z-ai/glm-5.3-flash | 1/1 | 100% |" in rendered


def test_no_digest_rides_the_metric_tags(tmp_path: Path) -> None:
    """A digest changes whenever a suite's cases change, so as a tag it is an unbounded cardinality
    leak. It rides the sweep's event instead, which is where a graph reads the break in its line."""
    metrics = _script("eval_sweep_metrics")
    _archive(tmp_path, "shard-a", "basics", passed=1, scored=1, digest="sha256:deadbeef")

    payload = metrics.series(tmp_path, mode="sweep", timestamp=1)
    event = metrics.digest_event(tmp_path, mode="sweep", run_url="https://run.invalid")

    assert all("deadbeef" not in tag for point in payload["series"] for tag in point["tags"])
    assert all(
        sorted(point["tags"])
        == [
            "mode:sweep",
            "shard:shard-a",
            "suite:basics",
            "target_model:claude-opus-5",
        ]
        for point in payload["series"]
    )
    assert "basics claude-opus-5 sha256:deadbeef" in event["text"]


def test_memory_ingestion_state_reports_fact_and_empty_evidence_counts(tmp_path: Path) -> None:
    metrics = _script("eval_sweep_metrics")
    _archive(tmp_path, "memory-ingestion", "memory_ingestion.locomo.multi_hop", 1, 2, "digest")
    readiness = IngestionReadiness(
        snapshot_digest="snapshot",
        corpus_digest="corpus",
        derivation_model=DERIVATION_MODEL,
        workspace_id=uuid4(),
        source_id=uuid4(),
        pages_root=tmp_path / "pages",
        page_count=9,
        memory_count=41,
        chunk_count=41,
        asker_email="asker@example.com",
        evidence=(
            DerivedEvidence(source_ref="one", memory_ids=(uuid4(),)),
            DerivedEvidence(source_ref="two", memory_ids=()),
        ),
    )
    model = metrics.NIGHTLY_MODELS[0]
    state = tmp_path / metrics.MEMORY_STATE_ROOT / model.label / "readiness.json"
    state.parent.mkdir(parents=True)
    state.write_text(readiness.model_dump_json())

    payload = metrics.series(tmp_path, mode="sweep", timestamp=1)
    values = {
        point["metric"]: point["points"][0]["value"]
        for point in payload["series"]
        if point["metric"].startswith("ufo.evals.memory_ingestion.")
    }

    assert values == {
        metrics.MEMORY_PAGE_METRIC: 9,
        metrics.MEMORY_FACT_METRIC: 41,
        metrics.MEMORY_CHUNK_METRIC: 41,
        metrics.MEMORY_EVIDENCE_METRIC: 2,
        metrics.MEMORY_EMPTY_EVIDENCE_METRIC: 1,
    }
    memory_points = [
        point
        for point in payload["series"]
        if point["metric"].startswith("ufo.evals.memory_ingestion.")
    ]
    assert all(f"target_model:{model.id}" in point["tags"] for point in memory_points)


def test_a_sweep_that_scored_nothing_submits_nothing(tmp_path: Path) -> None:
    metrics = _script("eval_sweep_metrics")
    (tmp_path / "runs").mkdir()

    with pytest.raises(SystemExit, match="recorded no score"):
        metrics.series(tmp_path, mode="sweep", timestamp=1)


def test_the_trend_point_lands_after_the_archive_it_refers_to(workflow) -> None:
    """Datadog holds a point for years; the artifact expires in 90 days, so S3 is what substantiates
    an old one. Submitting before the S3 copy lands would leave a trend point with no records."""
    named = [
        step.get("name") or step.get("uses") or "" for step in workflow["jobs"]["archive"]["steps"]
    ]

    assert named.index("Upload the sweep archive") < named.index("Archive the sweep")
    assert named.index("Archive the sweep") < named.index("Require a comparable cohort")
    assert named.index("Require a comparable cohort") < named.index("Report the scores to Datadog")
    assert named.index("Archive the sweep") < named.index("Report the scores to Datadog")


def test_a_pull_request_smoke_is_not_a_trend_point(workflow) -> None:
    report = next(
        step
        for step in workflow["jobs"]["archive"]["steps"]
        if step.get("name") == "Report the scores to Datadog"
    )

    assert report["if"] == "github.event_name != 'pull_request'"
    assert "api/v2/series" in report["run"] and "api/v1/events" in report["run"]
    assert workflow["env"]["SWEEP_MODE"].strip().endswith("'smoke' || 'sweep' }}")
