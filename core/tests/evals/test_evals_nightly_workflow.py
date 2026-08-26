import dataclasses
import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import yaml

from evals.memory_ingestion.assets import LOCOMO, LONGMEM_CLEANED
from evals.memory_ingestion.materialize import (
    DERIVATION_MODEL,
    DerivedEvidence,
    IngestionReadiness,
)
from evals.registry import TASKS
from evals.stack import Matrix

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

    assert [shard.pack for shard in shards] == [arm.pack for arm in planner.ARMS]
    assert all(planner.SMOKE_PROBE in shard.suites for shard in shards)
    assert sum(len(shard.suites) for shard in shards) < len(TASKS)


def test_a_written_shard_is_input_the_stack_accepts(planner, tmp_path: Path) -> None:
    for shard in (*planner.plan(smoke=False), *planner.plan(smoke=True)):
        directory = tmp_path / shard.label
        planner.write(shard, directory)
        matrix = Matrix.model_validate(tomllib.loads((directory / "matrix.toml").read_text()))
        config = tomllib.loads((directory / "ufo.toml").read_text())

        assert config["pack"]["name"] == shard.pack
        assert [spec.label for spec in matrix.run] == [shard.label]
        assert matrix.run[0].args[-len(shard.suites) :] == shard.suites
        if shard.agent is not None:
            assert matrix.run[0].args[:4] == (
                "--concurrency",
                str(planner.CONCURRENCY),
                "--agent",
                shard.agent,
            )


def test_the_workflow_fans_out_over_the_planned_shards(workflow) -> None:
    sweep = workflow["jobs"]["sweep"]
    plan = workflow["jobs"]["plan"]

    assert sweep["needs"] == ["plan", "sandbox-client"]
    assert sweep["strategy"]["fail-fast"] is False
    assert sweep["strategy"]["matrix"]["label"] == "${{ fromJSON(needs.plan.outputs.shards) }}"
    assert '--plan "$SWEEP_SMOKE"' in plan["steps"][-1]["run"]


def test_every_sweep_shard_receives_one_shared_sandbox_client(workflow) -> None:
    producer = workflow["jobs"]["sandbox-client"]
    upload = next(
        step for step in producer["steps"] if step.get("uses", "").startswith("actions/upload")
    )

    assert upload["with"] == {
        "name": "sandbox-client",
        "path": "client/target/release/ufo",
        "if-no-files-found": "error",
        "retention-days": 1,
    }
    sweep = workflow["jobs"]["sweep"]
    download = next(
        step
        for step in sweep["steps"]
        if step.get("uses", "").startswith("actions/download-artifact")
        and step["with"].get("name") == "sandbox-client"
    )
    install = sweep["steps"][sweep["steps"].index(download) + 1]

    assert "sandbox-client" in sweep["needs"]
    assert download["with"]["path"] == "client/target/release"
    assert "chmod +x client/target/release/ufo" in install["run"]
    assert 'client/target/release" >> "$GITHUB_PATH"' in install["run"]
    assert "needs" not in workflow["jobs"]["memory-ingestion"]


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
        "eval-run-records-${{ matrix.label }}",
        "eval-stack-logs-${{ matrix.label }}",
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
    assert "planned suites produced no report" in rendered
    assert all(suite in rendered for suite in planner.SMOKE_SUITES)


def test_the_summary_requires_every_memory_ingestion_report(memory_nightly, tmp_path: Path) -> None:
    summary = _script("eval_sweep_summary")
    (tmp_path / "runs").mkdir()

    rendered = summary.render(tmp_path, smoke=True, memory_ingestion=True)

    assert all(name in rendered for name in memory_nightly.SMOKE_REPORT_CASES)


def test_memory_ingestion_inputs_pin_luna_and_the_corpus(memory_nightly, tmp_path: Path) -> None:
    root = tmp_path / "input"
    snapshot = tmp_path / "snapshot"

    memory_nightly.write_inputs(root, snapshot)

    config = tomllib.loads((root / "ufo.toml").read_text())
    matrix = Matrix.model_validate(tomllib.loads((root / "matrix.toml").read_text()))
    assert config["models"]["background_jobs_model"] == DERIVATION_MODEL
    assert config["pack"]["name"] == "assistant"
    assert matrix.run[0].label == "memory-ingestion"
    assert matrix.run[0].memory_ingestion == snapshot.resolve()


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
    assert fetch["env"]["LONGMEM_URL"] == LONGMEM_CLEANED.url
    assert fetch["env"]["LOCOMO_URL"] == LOCOMO.url
    assert '"$SWEEP_SMOKE"' in prepare["run"] and '"$SWEEP_SMOKE"' in run["run"]
    assert "nightly_memory_ingestion.py verify" in run["run"]
    assert uploads == [
        "eval-run-records-memory-ingestion",
        "eval-memory-ingestion-state",
        "eval-stack-logs-memory-ingestion",
    ]


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

    memory_nightly.verify(reports, runs_root, output, smoke=True)
    assert output.read_text() == state.read_text()

    report_rows[0]["cases"].pop()
    (reports / "runs" / "run.json").write_text(json.dumps(run))
    with pytest.raises(RuntimeError, match="report cases differ"):
        memory_nightly.verify(reports, runs_root, output, smoke=True)


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
    assert state["with"]["pattern"] == "eval-memory-ingestion-state"
    assert state["with"]["merge-multiple"] is True
    assert state["with"]["path"] == str(Path("eval-reports") / metrics.MEMORY_STATE.parent)


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


def _archive(root: Path, label: str, name: str, passed: int, scored: int, digest: str) -> None:
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
        "id": str(uuid5(NAMESPACE_URL, f"{label}/{name}")),
        "created_at": "2026-08-19T05:00:00Z",
        "label": label,
        "agent": "assistant",
        "ufo_version": "0",
        "revision": "0",
        "reports": [{"name": name, "suite": "capability", "digest": digest, "cases": cases}],
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


def test_no_digest_rides_the_metric_tags(tmp_path: Path) -> None:
    """A digest changes whenever a suite's cases change, so as a tag it is an unbounded cardinality
    leak. It rides the sweep's event instead, which is where a graph reads the break in its line."""
    metrics = _script("eval_sweep_metrics")
    _archive(tmp_path, "shard-a", "basics", passed=1, scored=1, digest="sha256:deadbeef")

    payload = metrics.series(tmp_path, mode="sweep", timestamp=1)
    event = metrics.digest_event(tmp_path, mode="sweep", run_url="https://run.invalid")

    assert all("deadbeef" not in tag for point in payload["series"] for tag in point["tags"])
    assert all(
        sorted(point["tags"]) == ["mode:sweep", "shard:shard-a", "suite:basics"]
        for point in payload["series"]
    )
    assert "sha256:deadbeef" in event["text"] and "basics" in event["text"]


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
    state = tmp_path / metrics.MEMORY_STATE
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
