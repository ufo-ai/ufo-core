import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
MONITORS = ROOT / "infra" / "envs" / "testing" / "monitors.tf"
DEPLOY_ENVIRONMENTS = ("testing", "prod")
RUN_URL = "https://github.com/metalcraftai/ufo/actions/runs/30120902872"
DATADOG_STATUS_OK = 0
DATADOG_STATUS_CRITICAL = 2


def _code(source: str) -> str:
    return re.sub(r"(?m)^\s*(?:#|//).*$|\s+(?:#|//).*$", "", source)


def _deploy_change_gate():
    path = ROOT / ".github" / "scripts" / "deploy_change_gate.py"
    spec = importlib.util.spec_from_file_location("deploy_change_gate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    literals = {name: str(value) for name, value in environment.items() if "${{" not in str(value)}
    subprocess.run(
        ["bash", "-e", "-c", script],
        check=True,
        env={
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "CURL_PAYLOAD": str(payload),
            "CURL_URL": str(url),
            "DD_API_KEY": "deploy-reporter-key",
            **literals,
            "GATE_OUTCOME": outcome,
            "RUN_URL": RUN_URL,
        },
    )
    submitted = json.loads(payload.read_text())
    assert isinstance(submitted, list)
    assert len(submitted) == 1
    reported = submitted[0]
    assert isinstance(reported, dict)
    return url.read_text(), reported


def _facets(reported: dict[str, object]) -> set[str]:
    """The facet names a submitted check carries, which is all a monitor may group by."""
    tags = reported["tags"]
    assert isinstance(tags, list)
    named = {str(tag).split(":", 1)[0] for tag in tags}
    return named | {"host"} if reported.get("host_name") else named


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
        r"infra/(envs/(testing|prod|edge)|modules/(platform|edge)|templates)/)"
    )
    script = selector["run"]
    assert isinstance(script, str)
    assert 'git diff --name-only --no-renames "$BASE_SHA...$HEAD_SHA"' in script
    assert 'python .github/scripts/deploy_change_gate.py "$RUNNER_TEMP/deploy-paths"' in script
    assert 'if [ "$GITHUB_EVENT_NAME" != "pull_request" ]' in script
    assert "exit 0" not in script


def test_runtime_authorization_changes_are_split_across_deploys() -> None:
    gate = _deploy_change_gate()
    gate.validate_deploy_change(("infra/modules/platform/iam.tf",))
    gate.validate_deploy_change(("infra/modules/platform/ses.tf",))
    gate.validate_deploy_change(("core/src/ufo/serve.py",))
    gate.validate_deploy_change(
        ("infra/modules/platform/iam.tf", "core/tests/test_deploy_workflow.py")
    )
    with pytest.raises(ValueError, match="expand IAM, roll and drain"):
        gate.validate_deploy_change(
            ("infra/modules/platform/ses.tf", "control/src/ufo_control/gateway_email.py")
        )

    for runtime_path in (
        ".github/scripts/deploy_change_gate.py",
        "core/src/ufo/serve.py",
        "extensions/e2b/ufo_ext_e2b.py",
        "infra/envs/prod/ufo.tf",
        "infra/templates/hosted.yaml.tpl",
        "sandbox/build_template.py",
    ):
        with pytest.raises(ValueError, match="expand IAM, roll and drain"):
            gate.validate_deploy_change(("infra/modules/platform/iam.tf", runtime_path))


def test_select_step_executes_the_gate_across_triggers(tmp_path: Path) -> None:
    step = _step("changes", "Select deployment work")
    environment = step["env"]
    assert isinstance(environment, dict)
    assert "'origin/main'" in str(environment["BASE_SHA"])
    script = step["run"]
    assert isinstance(script, str)

    repo = tmp_path / "repo"
    scripts = repo / ".github" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / ".github" / "scripts" / "deploy_change_gate.py", scripts)
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python").symlink_to(sys.executable)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )

    def commit(*paths: str) -> str:
        for path in paths:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path)
        git("add", "-A")
        git("commit", "-m", paths[0] if paths else "seed")
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def run_select(event: str, base: str, head: str) -> tuple[int, str, str]:
        output = tmp_path / "github-output"
        output.write_text("")
        run = subprocess.run(
            ["bash", "-e", "-c", script],
            cwd=repo,
            capture_output=True,
            text=True,
            env={
                "PATH": f"{shim}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(tmp_path),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_EVENT_NAME": event,
                "BASE_SHA": base,
                "HEAD_SHA": head,
                "DEPLOY_PATHS_PATTERN": str(environment["DEPLOY_PATHS_PATTERN"]),
            },
        )
        return run.returncode, output.read_text(), run.stderr

    git("init", "-b", "main")
    seed = commit()

    git("checkout", "-b", "production-plan")
    production_head = commit("infra/envs/prod/main.tf")
    code, output, stderr = run_select("pull_request", seed, production_head)
    assert code == 0, stderr
    assert "deploy=true" in output

    git("checkout", "main")
    runtime_head = commit("core/src/ufo/serve.py")

    code, output, stderr = run_select("push", seed, runtime_head)
    assert code == 0, stderr
    assert "deploy=true" in output

    git("checkout", "-b", "boundary")
    boundary_head = commit("infra/modules/platform/iam.tf", "core/src/ufo/loop/engine.py")
    git("update-ref", "refs/remotes/origin/main", runtime_head)
    git("branch", "-D", "main")
    code, output, stderr = run_select("workflow_dispatch", "origin/main", boundary_head)
    assert code == 1
    assert "expand IAM, roll and drain" in stderr
    assert "deploy=" not in output


def test_deploy_change_gate_entrypoint_exits_nonzero_on_the_boundary(tmp_path: Path) -> None:
    """The gate's CI contract is its exit code: the argv-read → validate → exit path CI actually
    invokes, not the imported function."""
    gate = ROOT / ".github" / "scripts" / "deploy_change_gate.py"
    paths = tmp_path / "deploy-paths"
    paths.write_text("infra/modules/platform/iam.tf\ncore/src/ufo/serve.py\n")
    rejected = subprocess.run(
        [sys.executable, str(gate), str(paths)], capture_output=True, text=True
    )
    assert rejected.returncode == 1
    assert "expand IAM, roll and drain" in rejected.stderr
    paths.write_text("core/src/ufo/serve.py\n")
    allowed = subprocess.run(
        [sys.executable, str(gate), str(paths)], capture_output=True, text=True
    )
    assert allowed.returncode == 0
    assert allowed.stderr == ""


def test_plans_run_only_for_selected_deployment_inputs() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    for name in ("rollout", "edge"):
        job = jobs[name]
        assert isinstance(job, dict)
        assert job["needs"] == "changes"
        assert job["if"] == "needs.changes.outputs.deploy == 'true'"

    production = jobs["production"]
    assert isinstance(production, dict)
    assert production["needs"] == "changes"
    assert production["if"] == (
        "github.event_name == 'pull_request' && needs.changes.outputs.deploy == 'true'"
    )


def test_pull_requests_plan_production_foundation_without_applying() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    rollout_steps = rollout["steps"]
    assert isinstance(rollout_steps, list)
    image_tag = next(step for step in rollout_steps if step.get("name") == "Image tag")
    assert 'echo "IMAGE_TAG=$TAG" >> "$GITHUB_ENV"' in image_tag["run"]

    production = jobs["production"]
    assert isinstance(production, dict)
    assert production["env"] == {"TF_DIR": "infra/envs/prod"}
    steps = production["steps"]
    assert isinstance(steps, list)
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    assert "Terraform init" in names
    assert "Terraform foundation plan" in names
    assert not any("terraform apply" in step.get("run", "") for step in steps)
    plan = next(step for step in steps if step.get("name") == "Terraform foundation plan")
    assert plan["working-directory"] == "${{ env.TF_DIR }}"
    assert plan["env"] == {"TF_VAR_cloudflare_api_token": "${{ secrets.CLOUDFLARE_API_TOKEN }}"}
    assert (
        plan["run"] == "terraform plan -input=false -no-color -lock=false "
        "-target=module.platform.module.eks"
    )


def test_only_testing_owns_account_global_resources() -> None:
    ecr = _code((ROOT / "infra" / "modules" / "platform" / "ecr.tf").read_text())
    ses = _code((ROOT / "infra" / "modules" / "platform" / "ses.tf").read_text())

    ownership = re.compile(r"^\s*owns_account_resources\s*=\s*(true|false)\s*$", re.MULTILINE)
    terraform = tuple(
        path for path in (ROOT / "infra").rglob("*.tf") if ".terraform" not in path.parts
    )
    environment_sources = {
        root: _code("\n".join(path.read_text() for path in sorted(root.glob("*.tf"))))
        for root in (ROOT / "infra" / "envs").iterdir()
        if root.is_dir()
    }
    platform_sources = {
        root: source
        for root, source in environment_sources.items()
        if "../../modules/platform" in source
    }
    ownership_by_root = {}
    for root, source in platform_sources.items():
        matches = ownership.findall(source)
        assert len(matches) == 1, root
        ownership_by_root[root.relative_to(ROOT)] = matches[0]
    assert {root for root, owns in ownership_by_root.items() if owns == "true"} == {
        Path("infra/envs/testing")
    }
    assert ownership_by_root[Path("infra/envs/prod")] == "false"

    ecr_repository = re.search(
        r'^resource\s+"aws_ecr_repository"\s+"this"\s*{\s*$\n(.*?)^}\s*$',
        ecr,
        re.DOTALL | re.MULTILINE,
    )
    assert ecr_repository
    assert re.search(
        r"^\s*for_each\s*=\s*var\.owns_account_resources \? "
        r"toset\(local\.ecr_repositories\) : toset\(\[\]\)\s*$",
        ecr_repository.group(1),
        re.MULTILINE,
    )
    ses_identity = re.search(
        r'^resource\s+"aws_sesv2_email_identity"\s+"onboard"\s*{\s*$\n(.*?)^}\s*$',
        ses,
        re.DOTALL | re.MULTILINE,
    )
    assert ses_identity
    assert re.search(
        r"^\s*count\s*=\s*var\.owns_account_resources \? 1 : 0\s*$",
        ses_identity.group(1),
        re.MULTILINE,
    )
    identity_moves = [
        block
        for block in re.findall(r"^moved\s*{\s*$\n(.*?)^}\s*$", ses, re.DOTALL | re.MULTILINE)
        if re.search(r"^\s*from\s*=\s*aws_sesv2_email_identity\.onboard\s*$", block, re.MULTILINE)
    ]
    assert len(identity_moves) == 1
    assert re.search(
        r"^\s*to\s*=\s*aws_sesv2_email_identity\.onboard\[0\]\s*$",
        identity_moves[0],
        re.MULTILINE,
    )

    assert {
        path.relative_to(ROOT)
        for path in terraform
        if re.search(r'resource\s+"aws_ecr_repository"\s+"', path.read_text())
    } == {Path("infra/modules/platform/ecr.tf")}
    assert {
        path.relative_to(ROOT)
        for path in terraform
        if re.search(r'resource\s+"aws_sesv2_email_identity"\s+"', path.read_text())
    } == {Path("infra/modules/platform/ses.tf")}
    assert not {
        path.relative_to(ROOT)
        for path in terraform
        if re.search(r'data\s+"aws_sesv2_email_identity"\s+"', path.read_text())
    }
    assert {
        root.relative_to(ROOT)
        for root, source in environment_sources.items()
        if "_domainkey" in source or "module.platform.ses_dkim_records" in source
    } == {Path("infra/envs/testing")}
    assert (
        "module.platform.ses_dkim_records"
        in environment_sources[ROOT / "infra" / "envs" / "testing"]
    )

    prod = environment_sources[ROOT / "infra" / "envs" / "prod"]
    assert not re.search(r'^\s*provider\s+"cloudflare"\s*{', prod, re.MULTILINE)
    assert not re.search(r"^\s*cloudflare\s*=\s*{", prod, re.MULTILINE)
    assert (
        "registry.terraform.io/cloudflare/cloudflare"
        not in (ROOT / "infra" / "envs" / "prod" / ".terraform.lock.hcl").read_text()
    )


def test_proxy_gate_dials_the_rolled_proxy_with_the_shared_ca() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    steps = rollout["steps"]
    assert isinstance(steps, list)
    gate = next(step for step in steps if step.get("name") == "Gate sandbox egress proxy TLS")
    script = gate["run"]
    assert isinstance(script, str)
    assert "output -raw sandbox_proxy_ca_cert" in script
    assert "output -raw sandbox_proxy_url" in script
    assert "sandbox/proxy_gate.py" in script


def test_hosted_namespaces_hold_rollouts_until_nlb_targets_are_ready() -> None:
    for environment in DEPLOY_ENVIRONMENTS:
        terraform = (ROOT / "infra" / "envs" / environment / "ufo.tf").read_text()
        resource = re.search(
            r'^resource "kubernetes_namespace_v1" "ufo_system" \{\n(.*?)^}\n',
            terraform,
            re.DOTALL | re.MULTILINE,
        )
        assert resource, environment
        metadata = re.search(
            r"^  metadata \{\n(.*?)^  }\n",
            resource.group(1),
            re.DOTALL | re.MULTILINE,
        )
        assert metadata, environment
        labels = re.search(
            r"^    labels = \{\n(.*?)^    }\n",
            metadata.group(1),
            re.DOTALL | re.MULTILINE,
        )
        assert labels, environment
        readiness = re.search(
            r'^\s+"elbv2\.k8s\.aws/pod-readiness-gate-inject"\s*=\s*"([^"]+)"$',
            labels.group(1),
            re.MULTILINE,
        )
        assert readiness and readiness.group(1) == "enabled", environment


def test_sandbox_proxy_nlb_routes_across_all_enabled_zones() -> None:
    for environment in DEPLOY_ENVIRONMENTS:
        terraform = (ROOT / "infra" / "envs" / environment / "ufo.tf").read_text()
        cross_zone = re.search(
            r'"service\.beta\.kubernetes\.io/aws-load-balancer-attributes"\s*=\s*'
            r'"load_balancing\.cross_zone\.enabled=true"',
            terraform,
        )
        assert cross_zone, environment


def test_runtime_rollout_drains_before_the_proxy_gate() -> None:
    rollout = _workflow(WORKFLOWS / "deploy.yml")["jobs"]["rollout"]
    assert isinstance(rollout, dict)
    steps = rollout["steps"]
    assert isinstance(steps, list)
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    wait = names.index("Wait for runtime rollout")
    assert names.index("Terraform apply") < wait < names.index("Gate sandbox egress proxy TLS")
    step = steps[wait]
    assert isinstance(step, dict)
    script = step["run"]
    assert isinstance(script, str)
    assert "output -raw cluster_name" in script
    assert 'NAMESPACE="$(terraform -chdir="$TF_DIR" output -raw system_namespace)"' in script
    assert '--namespace "$NAMESPACE" rollout status deployment/ufo-sandbox-proxy' in script
    assert '--namespace "$NAMESPACE" rollout status deployment/ufo-serve' in script


def test_deployment_gate_joins_every_selected_result() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    deploy = jobs["deploy"]
    assert isinstance(deploy, dict)
    assert deploy["needs"] == ["changes", "rollout", "edge", "production"]
    assert deploy["if"] == "always()"
    steps = deploy["steps"]
    assert isinstance(steps, list)
    gate = steps[0]
    assert isinstance(gate, dict)
    environment = gate["env"]
    assert environment == {
        "CHANGES_RESULT": "${{ needs.changes.result }}",
        "DEPLOY_SELECTED": "${{ needs.changes.outputs.deploy }}",
        "EVENT_NAME": "${{ github.event_name }}",
        "ROLLOUT_RESULT": "${{ needs.rollout.result }}",
        "EDGE_RESULT": "${{ needs.edge.result }}",
        "PRODUCTION_RESULT": "${{ needs.production.result }}",
    }
    script = gate["run"]
    assert isinstance(script, str)
    for variable in environment:
        assert f'"${variable}"' in script
    assert gate["id"] == "gate"


@pytest.mark.parametrize(
    ("selected", "event", "rollout", "edge", "production", "accepted"),
    [
        ("true", "pull_request", "success", "success", "success", True),
        ("true", "push", "success", "success", "skipped", True),
        ("false", "push", "skipped", "skipped", "skipped", True),
        ("true", "pull_request", "success", "success", "skipped", False),
        ("true", "push", "success", "success", "success", False),
        ("true", "push", "failure", "success", "skipped", False),
        ("false", "push", "skipped", "skipped", "success", False),
        ("invalid", "push", "success", "success", "skipped", False),
    ],
)
def test_deployment_gate_accepts_only_expected_results(
    selected: str,
    event: str,
    rollout: str,
    edge: str,
    production: str,
    accepted: bool,
) -> None:
    gate = _step("deploy", "Require the selected deployment work")
    script = gate["run"]
    assert isinstance(script, str)
    environment = {
        "CHANGES_RESULT": "success",
        "DEPLOY_SELECTED": selected,
        "EDGE_RESULT": edge,
        "EVENT_NAME": event,
        "PRODUCTION_RESULT": production,
        "ROLLOUT_RESULT": rollout,
    }
    run = subprocess.run(["bash", "-e", "-c", script], env=os.environ | environment)
    assert (run.returncode == 0) is accepted


def test_every_main_deploy_conclusion_reaches_datadog(tmp_path: Path) -> None:
    step = _step("deploy", "Report the deploy conclusion to Datadog")
    assert step["if"] == "always() && github.ref_name == 'main'"

    api_url = re.search(r'^  api_url += +"(\S+)"$', MONITORS.read_text(), re.MULTILINE)
    assert api_url

    posted_to, failed = _report(tmp_path, "failure")
    assert urlparse(posted_to).hostname == urlparse(api_url.group(1)).hostname
    assert failed["message"] == RUN_URL

    _, succeeded = _report(tmp_path, "success")
    assert succeeded["message"] == RUN_URL


def test_the_deploy_monitor_watches_the_check_the_reporter_submits(tmp_path: Path) -> None:
    query = _monitor_attribute("deploy_failed", "query")
    parsed = re.fullmatch(
        r'"([\w.]+)"\.over\("([^"]+)"\)\.by\("([^"]+)"\)\.last\((\d+)\)\.count_by_status\(\)', query
    )
    assert parsed
    check, scope, grouping = parsed.group(1), parsed.group(2), parsed.group(3)
    submissions = int(parsed.group(4))
    assert 1 <= int(_monitor_attribute("deploy_failed", "critical")) <= submissions

    _, failed = _report(tmp_path, "failure")
    _, succeeded = _report(tmp_path, "success")
    assert failed["check"] == check
    assert succeeded["check"] == check
    assert scope in failed["tags"]
    assert scope in succeeded["tags"]
    assert grouping in _facets(failed)
    assert failed["status"] == DATADOG_STATUS_CRITICAL
    assert succeeded["status"] == DATADOG_STATUS_OK


def test_every_monitor_notifies_a_reachable_handle() -> None:
    monitors = re.findall(r'resource "datadog_monitor" "(\w+)"', MONITORS.read_text())
    assert monitors
    for monitor in monitors:
        message = _monitor_attribute(monitor, "message")
        assert re.search(r"@(?:slack-[\w-]+|[\w.-]+@[\w.-]+)", message), monitor
