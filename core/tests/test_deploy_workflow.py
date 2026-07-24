import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
MONITORS = ROOT / "infra" / "envs" / "testing" / "monitors.tf"
RUN_URL = "https://github.com/metalcraftai/ufo/actions/runs/30120902872"


def _workflow(path: Path) -> dict[str, object]:
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def _step(job: str, name: str) -> dict[str, object]:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    target = jobs[job]
    assert isinstance(target, dict)
    steps = target["steps"]
    assert isinstance(steps, list)
    found = [s for s in steps if isinstance(s, dict) and s.get("name") == name]
    assert len(found) == 1
    return found[0]


def _monitor_attribute(resource: str, attribute: str) -> str:
    block = re.search(
        rf'resource "datadog_monitor" "{resource}" {{\n(.*?)\n}}\n',
        MONITORS.read_text(),
        re.DOTALL,
    )
    assert block, resource
    value = re.search(rf"^ +{attribute} += +(.+)$", block.group(1), re.MULTILINE)
    assert value, attribute
    return value.group(1).strip('"').replace('\\"', '"')


CURL_STUB = """#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in
    -d) printf '%s' "$2" > "$CURL_PAYLOAD"; shift ;;
    https://*) printf '%s' "$1" > "$CURL_URL" ;;
  esac
  shift
done
"""


def _report(tmp_path: Path, outcome: str) -> tuple[str, dict[str, object]]:
    """Run the workflow's reporting step for one gate outcome, with curl replaced by a recorder."""
    step = _step("deploy", "Report the deploy conclusion to Datadog")
    environment = step["env"]
    assert isinstance(environment, dict)
    script = step["run"]
    assert isinstance(script, str)

    stubs = tmp_path / outcome
    stubs.mkdir()
    curl = stubs / "curl"
    curl.write_text(CURL_STUB)
    curl.chmod(0o755)
    payload = stubs / "payload.json"
    url = stubs / "url.txt"
    subprocess.run(
        ["bash", "-e", "-c", script],
        check=True,
        env={
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "CURL_PAYLOAD": str(payload),
            "CURL_URL": str(url),
            "DD_API_KEY": "deploy-reporter-key",
            "DD_EVENT_URL": str(environment["DD_EVENT_URL"]),
            "GATE_OUTCOME": outcome,
            "RUN_URL": RUN_URL,
        },
    )
    return url.read_text(), json.loads(payload.read_text())


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
    assert gate["id"] == "gate"


def test_every_main_deploy_conclusion_reaches_datadog(tmp_path: Path) -> None:
    step = _step("deploy", "Report the deploy conclusion to Datadog")
    assert step["if"] == "always() && github.ref_name == 'main'"

    api_url = re.search(r'^  api_url += +"(\S+)"$', MONITORS.read_text(), re.MULTILINE)
    assert api_url

    posted_to, failed = _report(tmp_path, "failure")
    assert urlparse(posted_to).hostname == urlparse(api_url.group(1)).hostname
    assert failed["title"] == "Deploy (testing) failed on main"
    assert failed["text"] == RUN_URL

    _, succeeded = _report(tmp_path, "success")
    assert succeeded["title"] == "Deploy (testing) succeeded on main"


def test_the_deploy_monitor_selects_exactly_the_failed_conclusion(tmp_path: Path) -> None:
    query = _monitor_attribute("deploy_failed", "query")
    parsed = re.fullmatch(r'events\("([^"]+)"\)\.rollup\("count"\)\.last\("15m"\) > (\d+)', query)
    assert parsed
    assert _monitor_attribute("deploy_failed", "critical") == parsed.group(2)
    terms = set(parsed.group(1).split())

    def facets(event: dict[str, object]) -> set[str]:
        tags = event["tags"]
        assert isinstance(tags, list)
        return {*tags, f"status:{event['alert_type']}"}

    _, failed = _report(tmp_path, "failure")
    _, succeeded = _report(tmp_path, "success")
    assert terms == facets(failed)
    assert not terms <= facets(succeeded)


def test_every_monitor_notifies_a_reachable_handle() -> None:
    monitors = re.findall(r'resource "datadog_monitor" "(\w+)"', MONITORS.read_text())
    assert monitors
    for monitor in monitors:
        message = _monitor_attribute(monitor, "message")
        assert re.search(r"@(?:slack-[\w-]+|[\w.-]+@[\w.-]+)", message), monitor
