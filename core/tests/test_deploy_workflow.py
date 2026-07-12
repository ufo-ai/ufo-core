from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(path: Path) -> dict[str, object]:
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_every_main_push_triggers_deployment() -> None:
    workflow = _workflow(WORKFLOWS / "deploy.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert triggers["push"] == {"branches": ["main"]}


def test_every_pull_request_has_one_deployment_gate() -> None:
    workflow = _workflow(WORKFLOWS / "deploy.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert triggers["pull_request"] == ""

    producers = []
    for path in (*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")):
        jobs = _workflow(path).get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            if isinstance(job, dict) and job.get("name", job_id) == "deployment":
                producers.append(path.name)
    assert producers == ["deploy.yml"]


def test_pull_request_plans_active_deployment_inputs() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    changes = jobs["changes"]
    assert isinstance(changes, dict)
    steps = changes["steps"]
    assert isinstance(steps, list)
    selector = steps[1]
    assert isinstance(selector, dict)
    environment = selector["env"]
    assert isinstance(environment, dict)
    assert environment["DEPLOY_PATHS_PATTERN"] == (
        r"^(\.github/workflows/deploy\.yml$|"
        r"infra/(envs/(testing|edge)|modules/(platform|edge)|templates)/)"
    )
    script = selector["run"]
    assert isinstance(script, str)
    assert 'git diff --name-only --no-renames "$BASE_SHA...$HEAD_SHA"' in script


def test_plans_run_only_for_selected_deployment_inputs() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    for name in ("rollout", "edge"):
        job = jobs[name]
        assert isinstance(job, dict)
        assert job["needs"] == "changes"
        assert job["if"] == "needs.changes.outputs.deploy == 'true'"


def test_deployment_gate_joins_platform_and_edge_results() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    deploy = jobs["deploy"]
    assert isinstance(deploy, dict)
    assert deploy["needs"] == ["changes", "rollout", "edge"]
    assert deploy["if"] == "always()"
    steps = deploy["steps"]
    assert isinstance(steps, list)
    gate = steps[0]
    assert isinstance(gate, dict)
    environment = gate["env"]
    assert environment == {
        "CHANGES_RESULT": "${{ needs.changes.result }}",
        "DEPLOY_SELECTED": "${{ needs.changes.outputs.deploy }}",
        "ROLLOUT_RESULT": "${{ needs.rollout.result }}",
        "EDGE_RESULT": "${{ needs.edge.result }}",
    }
    script = gate["run"]
    assert isinstance(script, str)
    for variable in environment:
        assert f'"${variable}"' in script
