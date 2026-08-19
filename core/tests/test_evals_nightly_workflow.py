import dataclasses
import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
import yaml

from evals.registry import TASKS
from evals.stack import Matrix

ROOT = Path(__file__).parents[2]
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


def test_the_workflow_fans_out_over_the_planned_shards(workflow) -> None:
    sweep = workflow["jobs"]["sweep"]
    plan = workflow["jobs"]["plan"]

    assert sweep["needs"] == "plan"
    assert sweep["strategy"]["fail-fast"] is False
    assert sweep["strategy"]["matrix"]["label"] == "${{ fromJSON(needs.plan.outputs.shards) }}"
    assert '--plan "$SWEEP_SMOKE"' in plan["steps"][-1]["run"]


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
    assert archive["if"] == "always()" and archive["needs"] == "sweep"
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
