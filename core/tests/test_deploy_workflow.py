import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from ufo.sources.sync import SOURCE_SYNC_FAILED_METRIC

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
DEPLOY_ENVIRONMENTS = ("testing", "prod")
MONITORS = {
    environment: ROOT / "infra" / "envs" / environment / "monitors.tf"
    for environment in DEPLOY_ENVIRONMENTS
}
RUN_URL = "https://github.com/metalcraftai/ufo/actions/runs/30120902872"
DATADOG_STATUS_OK = 0
DATADOG_STATUS_CRITICAL = 2
M6I_LARGE_DEFAULT_VCPUS = 2
STORAGE_QUERY = (
    "min(last_30m):avg:aws.rds.free_storage_space{dbinstanceidentifier:"
    "${module.platform.db_instance_identifier}} / avg:aws.rds.total_storage_space"
    "{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 0.05"
)


def _code(source: str) -> str:
    return re.sub(r"(?m)^\s*(?:#|//).*$|\s+(?:#|//).*$", "", source)


def _terraform_block(source: str, kind: str, name: str) -> str:
    block = re.search(
        rf'^{kind} "[^"]+" "{name}" {{\n(.*?)^}}\n',
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert block, name
    return block.group(1)


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


def _monitor_attribute(resource: str, attribute: str, environment: str = "testing") -> str:
    block = re.search(
        rf'resource "datadog_monitor" "{resource}" {{\n(.*?)\n}}\n',
        MONITORS[environment].read_text(),
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

AWS_STUB = """#!/bin/sh
printf '%s\n' "$*" >> "$AWS_CALLS"
[ "$AWS_FAIL" != "$1 $2" ] || exit 42
if [ "$AWS_FAIL_ONCE" = "$1 $2" ] && [ ! -e "$AWS_FAILED_ONCE" ]; then
  touch "$AWS_FAILED_ONCE"
  exit 42
fi
case "$1 $2" in
  "sts get-caller-identity") printf '%s\n' "$AWS_ACCOUNT" ;;
  "sesv2 get-account") printf '%s %s\n' "$SES_ACCESS" "$SES_SENDING" ;;
  "service-quotas get-service-quota")
    case "$*" in
      *L-1216C47A*) printf '%s\n' "$AWS_VCPU_QUOTA" ;;
      *L-69A177A2*) printf '%s\n' "$AWS_NLB_QUOTA" ;;
      *) printf '%s\n' "$AWS_QUOTA" ;;
    esac
    ;;
  "ec2 describe-availability-zones") printf '%s\n' 'us-east-1a us-east-1b us-east-1c' ;;
  "ec2 describe-instances")
    case "$*" in
      *prod-cluster*) printf '%s\n' "$AWS_INSTANCES_OWNED" ;;
      *) printf '%s\n' "$AWS_INSTANCES_USED" ;;
    esac
    ;;
  "ec2 describe-instance-types") printf '%s\n' "$AWS_DEFAULT_VCPUS" ;;
  "elbv2 describe-load-balancers") printf '%s\n' "$AWS_NLB_USED" ;;
  "resourcegroupstaggingapi get-resources") printf '%s\n' "$AWS_NLB_OWNED" ;;
  "ec2 describe-nat-gateways")
    case "$*" in
      *"tag:flyingobject.ai/environment,Values=prod"*) printf '%s\n' "$AWS_PROD_NAT_SUBNETS" ;;
      *) printf '%s\n' "$AWS_NAT_SUBNETS" ;;
    esac
    ;;
  "ec2 describe-subnets")
    case "$*" in
      *subnet-a*) printf '%s\n' 'us-east-1a' ;;
      *subnet-b*) printf '%s\n' 'us-east-1b' ;;
      *subnet-c*) printf '%s\n' 'us-east-1c' ;;
    esac
    ;;
  *)
    case "$*" in
      *"tag:flyingobject.ai/environment,Values=prod"*|*prod-cluster*|*prod-postgres*|*prod-redis-*)
        case "$*" in
          *"--output json"*) printf '%s\n' "$AWS_OWNED" ;;
          *) printf '%s\n' "$AWS_TEXT_OWNED" ;;
        esac
        ;;
      *)
        case "$*" in
          *"--output json"*) printf '%s\n' "$AWS_USED" ;;
          *) printf '%s\n' "$AWS_TEXT_USED" ;;
        esac
        ;;
    esac
    ;;
esac
"""

DOOR_CURL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$DOOR_CALLS"
URL=""
FORMAT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -w) FORMAT="$2"; shift ;;
    https://*) URL="$1" ;;
  esac
  shift
done
HOST="${URL#https://}"
HOST="${HOST%%/*}"
case "$URL" in
  */login)
    [ "$BAD_LOGIN_HOST" != "$HOST" ] || HOST=wrong.example
    printf '%s' "$FORMAT" |
      sed -e 's/%{http_code}/302/g' -e "s|%{redirect_url}|https://app.$HOST/login|g"
    ;;
  */ufo)
    [ "$BAD_HOST" != "$HOST" ] || HOST=wrong.example
    printf 'UFO_URL="${UFO_URL:-https://%s}"\\n' "$HOST"
    ;;
  */fleet)
    [ "$BAD_FLEET_HOST" != "$HOST" ] || { printf '{"craft":"unknown"}\\n'; exit; }
    [ "$NEGATIVE_FLEET_HOST" != "$HOST" ] || { printf '{"craft":-1}\\n'; exit; }
    printf '{"craft":1}\\n'
    ;;
  */)
    [ "$BAD_ROOT_HOST" != "$HOST" ] || HOST=wrong.example
    printf 'curl https://%s/waitlist\\n' "$HOST"
    ;;
esac
"""


def _run_production_prerequisites(
    tmp_path: Path,
    *,
    account: str = "899147036157",
    region: str = "us-east-1",
    ses_access: str = "True",
    ses_sending: str = "True",
    quota: str = "5",
    used: str = "0",
    text_used: str | None = None,
    failed_command: str = "",
    failed_once_command: str = "",
    nat_subnets: str = "subnet-a",
    owned: str = "0",
    text_owned: str | None = None,
    prod_nat_subnets: str = "",
    instances_used: str = "[]",
    instances_owned: str = "[]",
    default_vcpus: str = "2",
    vcpu_quota: str = "256",
    nlb_used: str = "0",
    nlb_owned: str = "0",
    nlb_quota: str = "50",
) -> tuple[subprocess.CompletedProcess[bytes], str]:
    script = _step("production_access", "Check production prerequisites")["run"]
    assert isinstance(script, str)
    aws = tmp_path / "aws"
    aws.write_text(AWS_STUB)
    aws.chmod(0o755)
    calls = tmp_path / "aws-calls"
    run = subprocess.run(
        ["bash", "-e", "-c", script],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "AWS_ACCOUNT": account,
            "AWS_CALLS": str(calls),
            "AWS_FAIL": failed_command,
            "AWS_FAILED_ONCE": str(tmp_path / "aws-failed-once"),
            "AWS_FAIL_ONCE": failed_once_command,
            "AWS_DEFAULT_VCPUS": default_vcpus,
            "AWS_INSTANCES_OWNED": instances_owned,
            "AWS_INSTANCES_USED": instances_used,
            "AWS_NAT_SUBNETS": nat_subnets,
            "AWS_NLB_OWNED": nlb_owned,
            "AWS_NLB_QUOTA": nlb_quota,
            "AWS_NLB_USED": nlb_used,
            "AWS_OWNED": owned,
            "AWS_PROD_NAT_SUBNETS": prod_nat_subnets,
            "AWS_QUOTA": quota,
            "AWS_REGION": region,
            "AWS_TEXT_OWNED": owned if text_owned is None else text_owned,
            "AWS_TEXT_USED": used if text_used is None else text_used,
            "AWS_USED": used,
            "AWS_VCPU_QUOTA": vcpu_quota,
            "SES_ACCESS": ses_access,
            "SES_SENDING": ses_sending,
        },
        capture_output=True,
    )
    return run, calls.read_text() if calls.exists() else ""


def _report(
    tmp_path: Path, testing_result: str, prod_result: str, edge_result: str = "success"
) -> tuple[str, dict[str, dict[str, object]]]:
    step = _step("deploy", "Report the deploy conclusion to Datadog")
    environment = step["env"]
    assert isinstance(environment, dict)
    script = step["run"]
    assert isinstance(script, str)

    stubs = tmp_path / f"{testing_result}-{prod_result}-{edge_result}"
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
            "TESTING_RESULT": testing_result,
            "PROD_RESULT": prod_result,
            "EDGE_RESULT": edge_result,
            "RUN_URL": RUN_URL,
        },
    )
    submitted = json.loads(payload.read_text())
    assert isinstance(submitted, list)
    assert len(submitted) == len(DEPLOY_ENVIRONMENTS)
    reports = {}
    for reported in submitted:
        assert isinstance(reported, dict)
        tags = reported["tags"]
        assert isinstance(tags, list)
        environment = next(str(tag).split(":", 1)[1] for tag in tags if str(tag).startswith("env:"))
        reports[environment] = reported
    assert set(reports) == set(DEPLOY_ENVIRONMENTS)
    return url.read_text(), reports


def _facets(reported: dict[str, object]) -> set[str]:
    tags = reported["tags"]
    assert isinstance(tags, list)
    named = {str(tag).split(":", 1)[0] for tag in tags}
    return named | {"host"} if reported.get("host_name") else named


@pytest.mark.parametrize("environment", DEPLOY_ENVIRONMENTS)
def test_database_storage_monitor_tracks_allocation(environment: str) -> None:
    assert _monitor_attribute("db_storage_low", "query", environment) == STORAGE_QUERY
    assert _monitor_attribute("db_storage_low", "critical", environment) == "0.05"
    assert _monitor_attribute("db_storage_low", "warning", environment) == "0.08"
    assert _monitor_attribute("db_storage_low", "evaluation_delay", environment) == "900"


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
        r"^(\.github/(workflows/deploy\.yml|scripts/terraform_plan_guard\.py)$|"
        r"infra/production_secrets\.py$|"
        r"infra/(production-access|envs/(testing|prod|edge)|modules/(platform|edge)|templates)/)"
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
        "infra/production_secrets.py",
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
    git("checkout", "-b", "guard")
    guard_head = commit(".github/scripts/terraform_plan_guard.py")
    code, output, stderr = run_select("pull_request", seed, guard_head)
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
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    assert rollout["needs"] == "changes"
    assert rollout["if"] == "needs.changes.outputs.deploy == 'true'"

    edge = jobs["edge"]
    assert isinstance(edge, dict)
    assert edge["needs"] == ["changes", "rollout"]
    assert edge["if"] == "needs.changes.outputs.deploy == 'true'"

    production = jobs["production"]
    assert isinstance(production, dict)
    assert production["needs"] == "changes"
    assert production["if"] == (
        "github.event_name == 'pull_request' && needs.changes.outputs.deploy == 'true'"
    )
    production_access = jobs["production_access"]
    assert isinstance(production_access, dict)
    assert production_access["needs"] == "changes"
    assert production_access["if"] == "needs.changes.outputs.deploy == 'true'"
    production_deploy = jobs["production_deploy"]
    assert isinstance(production_deploy, dict)
    assert production_deploy["needs"] == ["changes", "production_access", "rollout"]
    assert production_deploy["if"] == (
        "github.event_name != 'pull_request' && needs.changes.outputs.deploy == 'true'"
    )
    assert production_deploy["environment"] == "production"


@pytest.mark.parametrize(
    (
        "job_name",
        "targets",
        "plan_name",
        "guard_name",
        "apply_name",
        "gate_name",
        "host",
        "init_name",
        "plan_step_name",
        "condition",
    ),
    [
        (
            "edge",
            ("module.testing",),
            "edge",
            "Reject destructive changes",
            "Terraform apply",
            "Gate testing door",
            "testing.flyingobject.ai",
            "Terraform init",
            "Terraform plan",
            "github.event_name != 'pull_request'",
        ),
        (
            "production_deploy",
            (
                "cloudflare_ruleset.https_redirect",
                "cloudflare_zone_setting.always_use_https",
                "module.prod",
            ),
            "production-edge",
            "Reject destructive edge changes",
            "Terraform edge apply",
            "Gate production door",
            "flyingobject.ai",
            "Terraform edge init",
            "Terraform edge plan",
            None,
        ),
    ],
)
def test_edge_deploys_are_isolated(
    tmp_path: Path,
    job_name: str,
    targets: tuple[str, ...],
    plan_name: str,
    guard_name: str,
    apply_name: str,
    gate_name: str,
    host: str,
    init_name: str,
    plan_step_name: str,
    condition: str | None,
) -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    init = _step(job_name, init_name)
    plan = _step(job_name, plan_step_name)
    guard = _step(job_name, guard_name)
    apply = _step(job_name, apply_name)
    gate = _step(job_name, gate_name)
    plan_path = f"$RUNNER_TEMP/{plan_name}.tfplan"
    for step in (init, plan, guard, apply):
        assert step["working-directory"] == "infra/envs/edge"
    assert plan["env"] == {"TF_VAR_cloudflare_api_token": "${{ secrets.CLOUDFLARE_API_TOKEN }}"}
    assert "-lock-timeout=10m" in plan["run"]
    assert tuple(re.findall(r"-target=(\S+)", plan["run"])) == targets
    assert f'-out="{plan_path}"' in plan["run"]
    assert guard["run"] == (
        f'terraform show -json "{plan_path}" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert apply["run"] == f'terraform apply -input=false "{plan_path}"'
    assert steps.index(plan) < steps.index(guard) < steps.index(apply) < steps.index(gate)
    assert apply.get("if") == condition
    assert gate.get("if") == condition
    assert gate["shell"] == "bash"
    script = gate["run"]
    assert isinstance(script, str)
    curl = tmp_path / "curl"
    curl.write_text(DOOR_CURL_STUB)
    curl.chmod(0o755)
    calls = tmp_path / "door-calls"
    environment = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "BAD_HOST": "",
        "BAD_FLEET_HOST": "",
        "BAD_LOGIN_HOST": "",
        "BAD_ROOT_HOST": "",
        "NEGATIVE_FLEET_HOST": "",
        "DOOR_CALLS": str(calls),
    }
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], check=True, env=environment)
    invoked = calls.read_text().splitlines()
    assert len(invoked) == 4
    assert [call.rsplit(" ", 1)[-1] for call in invoked] == [
        f"https://{host}/",
        f"https://{host}/login",
        f"https://{host}/ufo",
        f"https://{host}/fleet",
    ]
    for name in (
        "BAD_ROOT_HOST",
        "BAD_LOGIN_HOST",
        "BAD_HOST",
        "NEGATIVE_FLEET_HOST",
        "BAD_FLEET_HOST",
    ):
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            capture_output=True,
            env=environment | {name: host},
        )
        assert failed.returncode != 0


@pytest.mark.parametrize(
    ("job_name", "group"),
    [
        (
            "edge",
            "${{ github.event_name == 'pull_request' && "
            "format('deploy-edge-pr-{0}', github.event.pull_request.number) || 'deploy-edge' }}",
        ),
        ("production_deploy", "deploy-edge"),
    ],
)
def test_edge_writers_share_deploy_concurrency(job_name: str, group: str) -> None:
    job = _workflow(WORKFLOWS / "deploy.yml")["jobs"][job_name]
    assert isinstance(job, dict)
    assert job["concurrency"] == {"group": group, "cancel-in-progress": "false"}


def test_pull_requests_guard_the_production_edge_plan() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    edge = jobs["edge"]
    assert isinstance(edge, dict)
    steps = edge["steps"]
    assert isinstance(steps, list)
    plan = _step("edge", "Terraform production edge plan")
    guard = _step("edge", "Reject destructive production edge changes")
    assert plan["if"] == "github.event_name == 'pull_request'"
    assert plan["working-directory"] == "infra/envs/edge"
    assert plan["run"] == (
        "terraform plan -input=false -no-color -lock=false \\\n"
        "  -target=cloudflare_ruleset.https_redirect \\\n"
        "  -target=cloudflare_zone_setting.always_use_https \\\n"
        "  -target=module.prod \\\n"
        '  -out="$RUNNER_TEMP/production-edge-review.tfplan"\n'
    )
    assert guard["if"] == "github.event_name == 'pull_request'"
    assert guard["working-directory"] == "infra/envs/edge"
    assert guard["run"] == (
        'terraform show -json "$RUNNER_TEMP/production-edge-review.tfplan" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert steps.index(plan) < steps.index(guard)
    assert not any(
        "terraform apply" in str(step.get("run", ""))
        and "production-edge-review.tfplan" in str(step.get("run", ""))
        for step in steps
        if isinstance(step, dict)
    )


def test_pull_requests_plan_production_foundation_without_applying() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    assert rollout["outputs"] == {
        "image_tag": "${{ steps.image_tag.outputs.image_tag }}",
        "sandbox_template": "${{ steps.sandbox_template.outputs.sandbox_template }}",
    }
    rollout_steps = rollout["steps"]
    assert isinstance(rollout_steps, list)
    image_tag = next(step for step in rollout_steps if step.get("name") == "Image tag")
    assert image_tag["id"] == "image_tag"
    assert 'echo "IMAGE_TAG=$TAG" >> "$GITHUB_ENV"' in image_tag["run"]
    assert 'echo "image_tag=$TAG" >> "$GITHUB_OUTPUT"' in image_tag["run"]
    sandbox_template = next(
        step for step in rollout_steps if step.get("name") == "Select sandbox template"
    )
    assert sandbox_template["id"] == "sandbox_template"
    assert "E2B_TEMPLATE=$(uv run python sandbox/build_template.py)" in sandbox_template["run"]
    assert 'echo "sandbox_template=$E2B_TEMPLATE" >> "$GITHUB_OUTPUT"' in sandbox_template["run"]

    production = jobs["production"]
    assert isinstance(production, dict)
    assert production["env"] == {
        "E2B_TEMPLATE": "ufo-sbx",
        "TF_DIR": "infra/envs/prod",
    }
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
        plan["run"] == "terraform plan -input=false -no-color -lock=false \\\n"
        '  -out="$RUNNER_TEMP/production.tfplan" \\\n'
        "  -target=module.platform.module.vpc \\\n"
        "  -target=module.platform.module.eks \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.api_keys \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.gateway_slack_connect \\\n"
        '  -var "e2b_template=$E2B_TEMPLATE" \\\n'
        '  -var "deployment_id=plan"\n'
    )


@pytest.mark.parametrize(
    ("account", "region", "ses_access", "ses_sending", "quota", "used", "accepted"),
    [
        ("899147036157", "us-east-1", "True", "True", "5", "0", True),
        ("111111111111", "us-east-1", "True", "True", "5", "0", False),
        ("899147036157", "us-west-2", "True", "True", "5", "0", False),
        ("899147036157", "us-east-1", "False", "True", "5", "0", False),
        ("899147036157", "us-east-1", "True", "False", "5", "0", False),
        ("899147036157", "us-east-1", "True", "True", "2", "0", False),
        ("899147036157", "us-east-1", "True", "True", "5", "5", False),
    ],
)
def test_production_prerequisites_fail_before_terraform(
    tmp_path: Path,
    account: str,
    region: str,
    ses_access: str,
    ses_sending: str,
    quota: str,
    used: str,
    accepted: bool,
) -> None:
    job = _workflow(WORKFLOWS / "deploy.yml")["jobs"]["production_access"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    preflight = _step("production_access", "Check production prerequisites")
    init = _step("production_access", "Terraform init")
    assert steps.index(preflight) < steps.index(init)
    run, invoked = _run_production_prerequisites(
        tmp_path,
        account=account,
        region=region,
        ses_access=ses_access,
        ses_sending=ses_sending,
        quota=quota,
        used=used,
    )
    assert (run.returncode == 0) is accepted
    if accepted:
        for code in (
            "L-F678F1CE",
            "L-0263D0A3",
            "L-1194D53C",
            "L-7B6409FD",
            "L-DFE45DF3",
            "L-1216C47A",
            "L-69A177A2",
            "L-FE5A380F",
        ):
            assert code in invoked
        assert invoked.count("describe-nat-gateways") == 2
        assert invoked.count("describe-subnets") == 1
        assert "Name=tag:aws:eks:cluster-name,Values=prod-cluster" in invoked
        assert "Key=elbv2.k8s.aws/cluster,Values=prod-cluster" in invoked
        instance_calls = [
            call for call in invoked.splitlines() if call.startswith("ec2 describe-instances ")
        ]
        assert len(instance_calls) == 2
        total = next(call for call in instance_calls if "prod-cluster" not in call)
        production = next(call for call in instance_calls if "prod-cluster" in call)
        assert "Name=instance-state-name,Values=pending,running,shutting-down" in total
        assert "Name=instance-state-name,Values=pending,running " in production
        assert "shutting-down" not in production
        assert invoked.count("Reservations[].Instances[].[InstanceType,InstanceLifecycle]") == 2
        assert "length(LoadBalancers[?Type == `network`])" in invoked
        assert "--resource-type-filters elasticloadbalancing:loadbalancer" in invoked
        assert "contains(ResourceARN, `:loadbalancer/net/`)" in invoked


@pytest.mark.parametrize(
    "failed_command",
    [
        "ec2 describe-vpcs",
        "ec2 describe-addresses",
        "eks list-clusters",
        "rds describe-db-instances",
        "elasticache describe-cache-clusters",
        "ec2 describe-instances",
        "ec2 describe-instance-types",
        "elbv2 describe-load-balancers",
        "resourcegroupstaggingapi get-resources",
        "ec2 describe-availability-zones",
    ],
)
def test_production_prerequisite_queries_fail_loud(tmp_path: Path, failed_command: str) -> None:
    instances_used = (
        '[["m6i.large", null]]' if failed_command == "ec2 describe-instance-types" else "[]"
    )
    run, _ = _run_production_prerequisites(
        tmp_path,
        failed_command=failed_command,
        instances_used=instances_used,
    )
    assert run.returncode == 42


@pytest.mark.parametrize(
    ("vcpu_quota", "nlb_quota"),
    [("15", "50"), ("256", "1")],
)
def test_production_prerequisites_reject_regional_capacity_shortages(
    tmp_path: Path, vcpu_quota: str, nlb_quota: str
) -> None:
    run, _ = _run_production_prerequisites(
        tmp_path,
        vcpu_quota=vcpu_quota,
        nlb_quota=nlb_quota,
    )
    assert run.returncode != 0


def test_production_vcpu_quota_uses_standard_on_demand_instance_defaults(
    tmp_path: Path,
) -> None:
    run, invoked = _run_production_prerequisites(
        tmp_path,
        instances_used=(
            '[["m6i.large", null], ["m6i.large", null], ["im4gn.large", null], '
            '["is4gen.medium", null], ["m6i.large", "spot"], ["g5.xlarge", null], '
            '["inf2.xlarge", null], ["mac2.metal", null], ["trn1.2xlarge", null], '
            '["hpc7g.4xlarge", null]]'
        ),
        instances_owned='[["m6i.large", null]]',
    )
    assert run.returncode == 0
    assert "ec2 L-1216C47A 26 8" in run.stdout.decode().splitlines()
    assert invoked.count("describe-instance-types") == 4
    assert (
        "ec2 describe-instance-types --instance-types m6i.large "
        "--query InstanceTypes[0].VCpuInfo.DefaultVCpus --output text"
    ) in invoked
    script = _step("production_access", "Check production prerequisites")["run"]
    assert isinstance(script, str)
    assert 'select(test("^(?:[acdhmrtz][0-9]|i(?:[0-9]|m[0-9]|s[0-9]))"))' in script


def test_production_vcpu_reservation_matches_the_node_group() -> None:
    production = (ROOT / "infra" / "envs" / "prod" / "main.tf").read_text()
    eks = (ROOT / "infra" / "modules" / "platform" / "eks.tf").read_text()
    instance_types = re.search(
        r'^  node_instance_types += +\["([^"]+)"\]$', production, re.MULTILINE
    )
    max_size = re.search(r"^  node_max_size += +(\d+)$", production, re.MULTILINE)
    az_count = re.search(r"^  az_count += +(\d+)$", production, re.MULTILINE)
    assert instance_types and instance_types.group(1) == "m6i.large"
    assert max_size and az_count
    assert "use_latest_ami_release_version = true" in eks

    script = _step("production_access", "Check production prerequisites")["run"]
    assert isinstance(script, str)
    reservation = re.search(
        r'REQUIRED=\$\(missing (\d+) "\$OWNED"\)\ncheck_headroom ec2 L-1216C47A',
        script,
    )
    assert reservation
    max_nodes = int(max_size.group(1))
    nodes = max_nodes + 2 * int(az_count.group(1))
    assert int(reservation.group(1)) == nodes * M6I_LARGE_DEFAULT_VCPUS


def test_production_nlb_reservation_matches_the_services() -> None:
    production = (ROOT / "infra" / "envs" / "prod" / "ufo.tf").read_text()
    load_balancers = production.count(
        '"service.beta.kubernetes.io/aws-load-balancer-nlb-target-type"'
    )
    script = _step("production_access", "Check production prerequisites")["run"]
    assert isinstance(script, str)
    reservation = re.search(
        r'REQUIRED=\$\(missing (\d+) "\$OWNED"\)\n'
        r"check_headroom elasticloadbalancing L-69A177A2",
        script,
    )
    assert reservation
    assert int(reservation.group(1)) == load_balancers == 2


def test_production_vcpu_inventory_parse_fails_loud(tmp_path: Path) -> None:
    run, _ = _run_production_prerequisites(tmp_path, instances_used="{")
    assert run.returncode != 0


def test_production_prerequisite_nat_lookup_fails_loud(tmp_path: Path) -> None:
    run, invoked = _run_production_prerequisites(
        tmp_path,
        failed_once_command="ec2 describe-subnets",
        nat_subnets="subnet-a subnet-b",
    )
    assert run.returncode == 42
    assert invoked.count("describe-subnets") == 1


def test_production_prerequisites_accept_no_nat_gateways(tmp_path: Path) -> None:
    run, invoked = _run_production_prerequisites(tmp_path, nat_subnets="")
    assert run.returncode == 0
    assert invoked.count("describe-nat-gateways") == 2
    assert invoked.count("Name=state,Values=pending,available,deleting") == 1
    assert (
        invoked.count(
            "Name=state,Values=pending,available Name=tag:flyingobject.ai/environment,Values=prod"
        )
        == 1
    )
    assert "describe-subnets" not in invoked
    assert invoked.count("L-FE5A380F") == 3
    assert "describe-availability-zones --filters Name=state,Values=available" in invoked


def test_production_prerequisites_report_computed_headroom(tmp_path: Path) -> None:
    run, invoked = _run_production_prerequisites(
        tmp_path,
        quota="10",
        used="2",
        owned="1",
        nat_subnets=" ".join(["subnet-a"] * 2 + ["subnet-b"] * 2 + ["subnet-c"] * 2),
        prod_nat_subnets="",
    )
    assert run.returncode == 0
    assert invoked.count("Name=tag:flyingobject.ai/environment,Values=prod") == 3
    assert run.stdout.decode().splitlines() == [
        "vpc L-F678F1CE 0 2",
        "ec2 L-0263D0A3 2 2",
        "eks L-1194D53C 0 2",
        "rds L-7B6409FD 0 2",
        "elasticache L-DFE45DF3 1 2",
        "ec2 L-1216C47A 28 0",
        "elasticloadbalancing L-69A177A2 2 0",
        "vpc L-FE5A380F 1 2",
        "vpc L-FE5A380F 1 2",
        "vpc L-FE5A380F 1 2",
    ]


def test_production_prerequisites_combine_paginated_inventory(tmp_path: Path) -> None:
    run, invoked = _run_production_prerequisites(
        tmp_path,
        quota="10",
        used="5",
        text_used="2\n3",
        owned="1",
        text_owned="0\n1",
        nat_subnets="",
    )
    assert run.returncode == 0
    assert invoked.count("--output json") == 14
    assert run.stdout.decode().splitlines() == [
        "vpc L-F678F1CE 0 5",
        "ec2 L-0263D0A3 2 5",
        "eks L-1194D53C 0 5",
        "rds L-7B6409FD 0 5",
        "elasticache L-DFE45DF3 1 5",
        "ec2 L-1216C47A 28 0",
        "elasticloadbalancing L-69A177A2 2 0",
        "vpc L-FE5A380F 1 0",
        "vpc L-FE5A380F 1 0",
        "vpc L-FE5A380F 1 0",
    ]


def test_production_prerequisites_reserve_only_missing_capacity(tmp_path: Path) -> None:
    instances = json.dumps([["m6i.large", None]] * 4)
    run, invoked = _run_production_prerequisites(
        tmp_path,
        quota="5",
        used="4",
        owned="3",
        instances_used=instances,
        instances_owned=instances,
        nlb_used="4",
        nlb_owned="2",
        nat_subnets=" ".join(["subnet-a"] * 4 + ["subnet-b"] * 4 + ["subnet-c"] * 4),
        prod_nat_subnets="subnet-a subnet-b subnet-c",
    )
    assert run.returncode == 0
    assert invoked.count("describe-subnets") == 15
    assert run.stdout.decode().splitlines() == [
        "vpc L-F678F1CE 0 4",
        "ec2 L-0263D0A3 0 4",
        "eks L-1194D53C 0 4",
        "rds L-7B6409FD 0 4",
        "elasticache L-DFE45DF3 0 4",
        "ec2 L-1216C47A 20 8",
        "elasticloadbalancing L-69A177A2 0 4",
        "vpc L-FE5A380F 0 4",
        "vpc L-FE5A380F 0 4",
        "vpc L-FE5A380F 0 4",
    ]


def test_hosted_runtime_receives_the_selected_sandbox_template() -> None:
    template = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    documents = template.split("\n---\n")
    for name in ("ufo-ingress", "ufo-serve"):
        deployment = next(
            document
            for document in documents
            if re.search(rf"^kind: Deployment\nmetadata:\n  name: {name}$", document, re.MULTILINE)
        )
        assert '- {name: E2B_TEMPLATE, value: "${e2b_template}"}' in deployment
    for environment in DEPLOY_ENVIRONMENTS:
        root = ROOT / "infra" / "envs" / environment
        assert (
            "e2b_template                     = var.e2b_template" in (root / "ufo.tf").read_text()
        )
        variables = (root / "variables.tf").read_text()
        assert 'variable "e2b_template"' in variables
        assert 'condition     = var.e2b_template != ""' in variables


@pytest.mark.parametrize(
    ("job_name", "plan_name", "step_name", "working_directory"),
    [
        ("rollout", "testing", "Terraform plan", "${{ env.TF_DIR }}"),
        ("edge", "edge", "Terraform plan", "infra/envs/edge"),
        ("production", "production", "Terraform foundation plan", "${{ env.TF_DIR }}"),
        (
            "production_access",
            "production-access",
            "Terraform plan",
            "infra/production-access",
        ),
        ("production_deploy", "production", "Terraform plan", "${{ env.TF_DIR }}"),
    ],
)
def test_saved_plans_reject_destructive_changes(
    job_name: str,
    plan_name: str,
    step_name: str,
    working_directory: str,
) -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    setup = next(step for step in steps if step.get("uses") == "hashicorp/setup-terraform@v3")
    assert setup["with"]["terraform_wrapper"] == "false"
    plan = _step(job_name, step_name)
    guard = _step(job_name, "Reject destructive changes")
    plan_path = f"$RUNNER_TEMP/{plan_name}.tfplan"
    assert f'-out="{plan_path}"' in plan["run"]
    assert plan["working-directory"] == working_directory
    assert guard["working-directory"] == working_directory
    assert guard.get("if") is None
    assert guard["run"] == (
        f'terraform show -json "{plan_path}" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert steps.index(plan) < steps.index(guard)


@pytest.mark.parametrize("job_name", ["rollout", "edge", "production_access"])
def test_mutating_plans_lock_state_and_preserve_inputs(job_name: str) -> None:
    script = _step(job_name, "Terraform plan")["run"]
    assert isinstance(script, str)
    assert "LOCK=true" in script
    assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then\n  LOCK=false\nfi' in script
    assert '-input=false -no-color -lock="$LOCK"' in script


def test_rollout_plan_pins_the_selected_artifacts() -> None:
    script = _step("rollout", "Terraform plan")["run"]
    assert '-var "image_tag=$IMAGE_TAG"' in script
    assert '-var "e2b_template=$E2B_TEMPLATE"' in script


@pytest.mark.parametrize(
    ("job_name", "plan_name", "step_name", "apply_condition"),
    [
        ("rollout", "testing", "Terraform plan", "github.event_name != 'pull_request'"),
        ("edge", "edge", "Terraform plan", "github.event_name != 'pull_request'"),
        (
            "production_access",
            "production-access",
            "Terraform plan",
            "github.event_name != 'pull_request'",
        ),
    ],
)
def test_apply_uses_the_guarded_plan(
    job_name: str,
    plan_name: str,
    step_name: str,
    apply_condition: str,
) -> None:
    plan = _step(job_name, step_name)
    guard = _step(job_name, "Reject destructive changes")
    apply = _step(job_name, "Terraform apply")
    assert plan.get("if") is None
    assert apply.get("if") == apply_condition
    assert apply["run"] == f'terraform apply -input=false "$RUNNER_TEMP/{plan_name}.tfplan"'
    steps = _workflow(WORKFLOWS / "deploy.yml")["jobs"][job_name]["steps"]
    assert steps.index(guard) < steps.index(apply)


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


def test_production_deploy_role_trusts_only_the_main_production_workflow() -> None:
    source = _code((ROOT / "infra" / "production-access" / "main.tf").read_text())
    trust = _terraform_block(source, "data", "github_trust")
    workflow_name = _workflow(WORKFLOWS / "deploy.yml")["name"]
    assert isinstance(workflow_name, str)
    assert [
        path
        for path in (*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))
        if _workflow(path).get("name") == workflow_name
    ] == [WORKFLOWS / "deploy.yml"]
    assert len(re.findall(r"^  statement \{", trust, re.MULTILINE)) == 1
    assert re.search(
        r'principals \{\n\s+type\s+=\s+"Federated"\n'
        r'\s+identifiers\s+=\s+\["arn:aws:iam::\$\{data\.aws_caller_identity\.current'
        r'\.account_id}:oidc-provider/token\.actions\.githubusercontent\.com"\]\n\s+}',
        trust,
    )
    conditions = {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": (
            "repo:${var.github_repo}:environment:production"
        ),
        "token.actions.githubusercontent.com:ref": "refs/heads/main",
        "token.actions.githubusercontent.com:environment": "production",
        "token.actions.githubusercontent.com:workflow": workflow_name,
    }
    assert len(re.findall(r"^    condition \{", trust, re.MULTILINE)) == len(conditions)
    for variable, value in conditions.items():
        assert re.search(
            r'condition \{\n\s+test\s+=\s+"StringEquals"\n'
            rf'\s+variable\s+=\s+"{re.escape(variable)}"\n'
            rf'\s+values\s+=\s+\["{re.escape(value)}"\]\n\s+}}',
            trust,
        )
    assert "pull_request" not in trust
    assert 'type = "AWS"' not in re.sub(r"\s+", " ", trust)

    role = _terraform_block(source, "resource", "github_production_deploy")
    assert re.search(r'^  name\s+=\s+"github-production-deploy"$', role, re.MULTILINE)
    assert re.search(
        r"^  assume_role_policy\s+=\s+data\.aws_iam_policy_document\.github_trust\.json$",
        role,
        re.MULTILINE,
    )
    attachment = _terraform_block(source, "resource", "administrator")
    assert re.search(
        r"^  role\s+=\s+aws_iam_role\.github_production_deploy\.name$",
        attachment,
        re.MULTILINE,
    )
    assert re.search(
        r'^  policy_arn\s+=\s+"arn:aws:iam::aws:policy/AdministratorAccess"$',
        attachment,
        re.MULTILINE,
    )
    prod = _code((ROOT / "infra" / "envs" / "prod" / "main.tf").read_text())
    assert (
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:"
        "role/github-production-deploy" in prod
    )
    assert "role/github-deploy" not in prod


def test_production_deploy_consumes_the_protected_role_and_artifacts() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["production_deploy"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"
    assert job["env"] == {
        "DEPLOY_ROLE_ARN": "arn:aws:iam::899147036157:role/github-production-deploy",
        "E2B_TEMPLATE": "${{ needs.rollout.outputs.sandbox_template }}",
        "IMAGE_TAG": "${{ needs.rollout.outputs.image_tag }}",
        "TF_DIR": "infra/envs/prod",
    }
    steps = job["steps"]
    assert isinstance(steps, list)
    credentials = next(
        step for step in steps if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
    )
    assert credentials["with"]["role-to-assume"] == "${{ env.DEPLOY_ROLE_ARN }}"


def test_production_deploy_rejects_missing_inputs_before_role_assumption() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["production_deploy"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    step = _step("production_deploy", "Require production inputs")
    assert steps[0] == step
    assert steps[1] == {"uses": "actions/checkout@v4"}
    assert step["env"] == {
        "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
        "BROWSERBASE_API_KEY": "${{ secrets.BROWSERBASE_API_KEY }}",
        "CLOUDFLARE_API_TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}",
        "DD_API_KEY": "${{ secrets.DD_API_KEY }}",
        "DD_APP_KEY": "${{ secrets.DD_APP_KEY }}",
        "E2B_API_KEY": "${{ secrets.E2B_API_KEY }}",
        "EXA_API_KEY": "${{ secrets.EXA_API_KEY }}",
        "OPENAI_API_KEY": "${{ secrets.OPENAI_API_KEY }}",
        "TURBOPUFFER_API_KEY": "${{ secrets.TURBOPUFFER_API_KEY }}",
    }
    required = (*step["env"], "E2B_TEMPLATE", "IMAGE_TAG")
    environment = dict.fromkeys(required, "present")
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], check=True, env=environment)
    for missing in required:
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
            capture_output=True,
            env=environment | {missing: ""},
        )
        assert failed.returncode != 0
        assert failed.stdout.decode() == f"::error::{missing} is required\n"
        assert b"present" not in failed.stdout + failed.stderr


def test_production_deploy_applies_guarded_foundation_then_runtime() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["production_deploy"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)

    foundation_plan = _step("production_deploy", "Terraform foundation plan")
    foundation_guard = _step("production_deploy", "Reject destructive foundation changes")
    foundation_apply = _step("production_deploy", "Terraform foundation apply")
    secrets = _step("production_deploy", "Write production runtime secrets")
    runtime_plan = _step("production_deploy", "Terraform plan")
    runtime_guard = _step("production_deploy", "Reject destructive changes")
    refresh = _step("production_deploy", "Refresh production runtime secrets")
    runtime_apply = _step("production_deploy", "Terraform apply")
    rollout = _step("production_deploy", "Wait for runtime rollout")
    for step in (foundation_plan, foundation_guard, foundation_apply):
        assert step["working-directory"] == "${{ env.TF_DIR }}"
    assert foundation_plan["run"] == (
        "terraform plan -input=false -no-color \\\n"
        '  -out="$RUNNER_TEMP/production-foundation.tfplan" \\\n'
        "  -target=module.platform.module.vpc \\\n"
        "  -target=module.platform.module.eks \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.postgres \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.platform \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.api_keys \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.gateway_slack_connect \\\n"
        '  -var "image_tag=$IMAGE_TAG" -var "e2b_template=$E2B_TEMPLATE" \\\n'
        '  -var "deployment_id=$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"\n'
    )
    assert foundation_guard["run"] == (
        'terraform show -json "$RUNNER_TEMP/production-foundation.tfplan" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert foundation_apply["run"] == (
        'terraform apply -input=false "$RUNNER_TEMP/production-foundation.tfplan"'
    )
    assert secrets == {
        "name": "Write production runtime secrets",
        "env": {
            "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
            "BROWSERBASE_API_KEY": "${{ secrets.BROWSERBASE_API_KEY }}",
            "DD_API_KEY": "${{ secrets.DD_API_KEY }}",
            "E2B_API_KEY": "${{ secrets.E2B_API_KEY }}",
            "EXA_API_KEY": "${{ secrets.EXA_API_KEY }}",
            "OPENAI_API_KEY": "${{ secrets.OPENAI_API_KEY }}",
            "TURBOPUFFER_API_KEY": "${{ secrets.TURBOPUFFER_API_KEY }}",
            "PRODUCTION_DEPLOYMENT_ID": "${{ github.run_id }}",
        },
        "run": (
            'PRODUCTION_API_KEYS_SECRET_ID="$(terraform -chdir="$TF_DIR" '
            'output -raw api_keys_secret_id)" \\\n'
            'PRODUCTION_GATEWAY_SECRET_ID="$(terraform -chdir="$TF_DIR" '
            'output -raw gateway_secret_id)" \\\n'
            "  python infra/production_secrets.py\n"
        ),
    }
    assert runtime_plan["run"] == (
        'terraform plan -input=false -no-color -out="$RUNNER_TEMP/production.tfplan" '
        '-var "image_tag=$IMAGE_TAG" -var "e2b_template=$E2B_TEMPLATE" '
        '-var "deployment_id=$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"'
    )
    assert runtime_guard["run"] == (
        'terraform show -json "$RUNNER_TEMP/production.tfplan" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert runtime_apply["run"] == 'terraform apply -input=false "$RUNNER_TEMP/production.tfplan"'
    assert refresh["run"] == (
        "aws eks update-kubeconfig \\\n"
        '  --region "$AWS_REGION" \\\n'
        '  --name "$(terraform -chdir="$TF_DIR" output -raw cluster_name)"\n'
        'NAMESPACE="$(terraform -chdir="$TF_DIR" output -raw system_namespace)"\n'
        "python infra/production_secret_sync.py \\\n"
        '  "$NAMESPACE" "$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"\n'
    )
    assert all(
        step.get("if") is None
        for step in (
            foundation_plan,
            foundation_guard,
            foundation_apply,
            runtime_plan,
            runtime_guard,
            secrets,
            refresh,
            runtime_apply,
            rollout,
        )
    )
    order = (
        foundation_plan,
        foundation_guard,
        foundation_apply,
        runtime_plan,
        runtime_guard,
        secrets,
        refresh,
        runtime_apply,
        rollout,
    )
    assert all(steps.index(before) < steps.index(after) for before, after in pairwise(order))


@pytest.mark.parametrize("job_name", ["rollout", "production_deploy"])
def test_proxy_gate_dials_the_rolled_proxy_with_the_shared_ca(job_name: str) -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    gate = next(step for step in steps if step.get("name") == "Gate sandbox egress proxy TLS")
    if job_name == "rollout":
        selector = next(step for step in steps if step.get("name") == "Select sandbox template")
        assert 'echo "E2B_TEMPLATE=$E2B_TEMPLATE" >> "$GITHUB_ENV"' in selector["run"]
        assert steps.index(selector) < steps.index(gate)
    else:
        assert job["env"]["E2B_TEMPLATE"] == "${{ needs.rollout.outputs.sandbox_template }}"
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
        _, start, remainder = terraform.partition("  sandbox_proxy_manifests = {")
        assert start, environment
        sandbox_proxy, end, _ = remainder.partition("\n  }\n\n  ufo_workload_manifests")
        assert end, environment
        cross_zone = re.search(
            r'"service\.beta\.kubernetes\.io/aws-load-balancer-attributes"\s*=\s*'
            r'"load_balancing\.cross_zone\.enabled=true"',
            sandbox_proxy,
        )
        assert cross_zone, environment


@pytest.mark.parametrize("job_name", ["rollout", "production_deploy"])
def test_runtime_rollout_drains_before_the_proxy_gate(job_name: str) -> None:
    job = _workflow(WORKFLOWS / "deploy.yml")["jobs"][job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
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
    ingress = (
        "kubectl --namespace ingress-nginx rollout status "
        "deployment/ingress-nginx-controller --timeout=15m"
    )
    assert (ingress in script) is (job_name == "production_deploy")
    templates = ROOT / "infra" / "templates"
    manifests = [
        document
        for source in (
            re.sub(
                r"(?m)^%\{ (?:if workload_ha|endif) \}\n?",
                "",
                (templates / f"{name}.yaml.tpl").read_text(),
            )
            for name in ("hosted", "observability", "cluster-services")
        )
        for document in yaml.safe_load_all(re.sub(r"\$\{([^}]+)\}", r"\1", source))
        if isinstance(document, dict)
    ]
    readiness_resources = {
        f"{document['kind'].lower()}/{document['metadata']['name']}"
        for document in manifests
        if document.get("kind") in {"Certificate", "ExternalSecret"}
    }
    rollout_resources = {
        f"{document['kind'].lower()}/{document['metadata']['name']}"
        for document in manifests
        if document.get("kind") in {"DaemonSet", "Deployment", "StatefulSet"}
    }
    assert readiness_resources == {
        "certificate/ufo-gateway-tls",
        "certificate/ufo-ingress-tls",
        "certificate/ufo-serve-tls",
        "externalsecret/ufo-control-secrets",
        "externalsecret/ufo-platform-secrets",
        "externalsecret/ufo-gateway-slack-connect",
        "externalsecret/ufo-egress-ca",
        "externalsecret/datadog-api-key",
    }
    assert rollout_resources == {
        "daemonset/otel-logs-agent",
        "deployment/otel-collector",
        "deployment/ufo-gateway",
        "deployment/ufo-sandbox-proxy",
        "deployment/ufo-ingress",
        "deployment/ufo-serve",
    }
    commands = [
        shlex.split(line)
        for line in script.replace("\\\n", " ").splitlines()
        if line.strip().startswith("kubectl ")
    ]
    namespaced = [command for command in commands if command[1:3] == ["--namespace", "$NAMESPACE"]]
    wait_commands = [command for command in namespaced if command[3] == "wait"]
    rollout_commands = [command for command in namespaced if command[3] == "rollout"]
    assert len(namespaced) == len(wait_commands) + len(rollout_commands)
    assert all(
        command[:6]
        == [
            "kubectl",
            "--namespace",
            "$NAMESPACE",
            "wait",
            "--for=condition=Ready",
            "--timeout=15m",
        ]
        for command in wait_commands
    )
    assert all(
        len(command) == 7
        and command[3:5] == ["rollout", "status"]
        and command[-1] == "--timeout=15m"
        for command in rollout_commands
    )
    gated_readiness = {resource for command in wait_commands for resource in command[6:]}
    gated_rollouts = {command[5] for command in rollout_commands}
    expected_production = job_name == "production_deploy"
    assert gated_readiness == (readiness_resources if expected_production else set())
    testing_rollouts = {
        "deployment/ufo-sandbox-proxy",
        "deployment/ufo-ingress",
        "deployment/ufo-serve",
    }
    assert gated_rollouts == (rollout_resources if expected_production else testing_rollouts)


def test_runtime_certificates_match_ingress_tls() -> None:
    source = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    documents = [
        yaml.safe_load(re.sub(r"\$\{([^}]+)\}", r"\1", document))
        for document in source.split("\n---\n")
        if re.search(r"^kind: (?:Ingress|Certificate)$", document, re.MULTILINE)
    ]
    ingresses = sorted(
        (
            tls["secretName"],
            tls["secretName"],
            tuple(tls["hosts"]),
        )
        for document in documents
        if document["kind"] == "Ingress"
        for tls in document["spec"]["tls"]
    )
    certificates = sorted(
        (
            document["metadata"]["name"],
            document["spec"]["secretName"],
            tuple(document["spec"]["dnsNames"]),
        )
        for document in documents
        if document["kind"] == "Certificate"
    )
    assert (
        certificates
        == ingresses
        == [
            ("ufo-gateway-tls", "ufo-gateway-tls", ("apex_host", "gateway_origin_host")),
            ("ufo-ingress-tls", "ufo-ingress-tls", ("*.apex_host",)),
            ("ufo-serve-tls", "ufo-serve-tls", ("shared_host",)),
        ]
    )


def test_runtime_secret_consumers_roll_once_per_production_deploy() -> None:
    templates = ROOT / "infra" / "templates"
    manifests = [
        document
        for source in (
            re.sub(
                r"(?m)^%\{ (?:if workload_ha|endif) \}\n?",
                "",
                (templates / name).read_text(),
            )
            for name in (
                "hosted.yaml.tpl",
                "observability.yaml.tpl",
                "cluster-services.yaml.tpl",
            )
        )
        for document in yaml.safe_load_all(re.sub(r"\$\{([^}]+)\}", r"\1", source))
        if isinstance(document, dict)
    ]
    secret_names = {
        document["spec"].get("target", {}).get("name", document["metadata"]["name"])
        for document in manifests
        if document.get("kind") == "ExternalSecret"
    }
    consumers = {
        document["metadata"]["name"]: document["spec"]["template"]["metadata"]["annotations"]
        for document in manifests
        if document.get("kind") in {"DaemonSet", "Deployment", "StatefulSet"}
        and any(name in json.dumps(document["spec"]["template"]["spec"]) for name in secret_names)
    }
    assert consumers == {
        name: {"flyingobject.ai/deployment-id": "deployment_id"}
        for name in (
            "otel-collector",
            "ufo-gateway",
            "ufo-ingress",
            "ufo-sandbox-proxy",
            "ufo-serve",
        )
    }
    production = (ROOT / "infra" / "envs" / "prod" / "ufo.tf").read_text()
    testing = (ROOT / "infra" / "envs" / "testing" / "ufo.tf").read_text()
    assert len(re.findall(r"deployment_id\s+= var\.deployment_id", production)) == 2
    assert len(re.findall(r'deployment_id\s+= "testing"', testing)) == 2


def test_deployment_gate_joins_every_selected_result() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    deploy = jobs["deploy"]
    assert isinstance(deploy, dict)
    assert deploy["needs"] == [
        "changes",
        "rollout",
        "edge",
        "production",
        "production_access",
        "production_deploy",
    ]
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
        "PRODUCTION_ACCESS_RESULT": "${{ needs.production_access.result }}",
        "PRODUCTION_DEPLOY_RESULT": "${{ needs.production_deploy.result }}",
    }
    script = gate["run"]
    assert isinstance(script, str)
    for variable in environment:
        assert f'"${variable}"' in script


@pytest.mark.parametrize(
    (
        "selected",
        "event",
        "rollout",
        "edge",
        "production",
        "access",
        "production_deploy",
        "accepted",
    ),
    [
        ("true", "pull_request", "success", "success", "success", "success", "skipped", True),
        ("true", "push", "success", "success", "skipped", "success", "success", True),
        ("false", "push", "skipped", "skipped", "skipped", "skipped", "skipped", True),
        ("true", "pull_request", "success", "success", "skipped", "success", "skipped", False),
        ("true", "pull_request", "success", "success", "success", "failure", "skipped", False),
        ("true", "pull_request", "success", "success", "success", "success", "success", False),
        ("true", "push", "success", "success", "success", "success", "success", False),
        ("true", "push", "failure", "success", "skipped", "success", "success", False),
        ("true", "push", "success", "success", "skipped", "failure", "success", False),
        ("true", "push", "success", "success", "skipped", "success", "failure", False),
        ("false", "push", "skipped", "skipped", "success", "skipped", "skipped", False),
        ("false", "push", "skipped", "skipped", "skipped", "success", "skipped", False),
        ("false", "push", "skipped", "skipped", "skipped", "skipped", "success", False),
        ("invalid", "push", "success", "success", "skipped", "success", "success", False),
    ],
)
def test_deployment_gate_accepts_only_expected_results(
    selected: str,
    event: str,
    rollout: str,
    edge: str,
    production: str,
    access: str,
    production_deploy: str,
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
        "PRODUCTION_ACCESS_RESULT": access,
        "PRODUCTION_DEPLOY_RESULT": production_deploy,
        "ROLLOUT_RESULT": rollout,
    }
    run = subprocess.run(["bash", "-e", "-c", script], env=os.environ | environment)
    assert (run.returncode == 0) is accepted


def test_every_main_deploy_conclusion_reaches_datadog(tmp_path: Path) -> None:
    step = _step("deploy", "Report the deploy conclusion to Datadog")
    assert step["if"] == "always() && github.ref_name == 'main'"
    assert step["env"] == {
        "DD_CHECK_URL": "https://api.us5.datadoghq.com/api/v1/check_run",
        "DEPLOY_CHECK": "ufo.deploy.main",
        "DD_STATUS_OK": "0",
        "DD_STATUS_CRITICAL": "2",
        "TESTING_RESULT": "${{ needs.rollout.result }}",
        "PROD_RESULT": "${{ needs.production_deploy.result }}",
        "EDGE_RESULT": "${{ needs.edge.result }}",
        "RUN_URL": (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
        ),
    }

    api_urls = {
        re.search(r'^  api_url += +"(\S+)"$', path.read_text(), re.MULTILINE).group(1)
        for path in MONITORS.values()
    }
    assert len(api_urls) == 1

    posted_to, testing_failed = _report(tmp_path, "failure", "skipped", "skipped")
    assert urlparse(posted_to).hostname == urlparse(api_urls.pop()).hostname
    assert {report["message"] for report in testing_failed.values()} == {RUN_URL}
    assert {report["status"] for report in testing_failed.values()} == {DATADOG_STATUS_CRITICAL}

    _, succeeded = _report(tmp_path, "success", "success")
    assert {report["message"] for report in succeeded.values()} == {RUN_URL}

    _, edge_failed = _report(tmp_path, "success", "success", "failure")
    assert edge_failed["testing"]["status"] == DATADOG_STATUS_CRITICAL
    assert edge_failed["prod"]["status"] == DATADOG_STATUS_OK

    _, edge_skipped = _report(tmp_path, "success", "success", "skipped")
    assert edge_skipped["testing"]["status"] == DATADOG_STATUS_CRITICAL
    assert edge_skipped["prod"]["status"] == DATADOG_STATUS_OK

    for prod_result in ("failure", "skipped"):
        _, prod_failed = _report(tmp_path, "success", prod_result)
        assert prod_failed["testing"]["status"] == DATADOG_STATUS_OK
        assert prod_failed["prod"]["status"] == DATADOG_STATUS_CRITICAL


@pytest.mark.parametrize("environment", DEPLOY_ENVIRONMENTS)
def test_the_deploy_monitor_watches_the_check_the_reporter_submits(
    tmp_path: Path, environment: str
) -> None:
    query = _monitor_attribute("deploy_failed", "query", environment)
    parsed = re.fullmatch(
        r'"([\w.]+)"\.over\("([^"]+)"\)\.by\("([^"]+)"\)\.last\((\d+)\)\.count_by_status\(\)', query
    )
    assert parsed
    check, scope, grouping = parsed.group(1), parsed.group(2), parsed.group(3)
    submissions = int(parsed.group(4))
    assert 1 <= int(_monitor_attribute("deploy_failed", "critical", environment)) <= submissions

    failed_results = (
        ("failure", "skipped", "skipped")
        if environment == "testing"
        else ("success", "skipped", "skipped")
    )
    _, failed_run = _report(tmp_path, *failed_results)
    _, succeeded = _report(tmp_path, "success", "success")
    failed = failed_run[environment]
    succeeded_report = succeeded[environment]
    assert failed["check"] == check
    assert succeeded_report["check"] == check
    assert scope in failed["tags"]
    assert scope in succeeded_report["tags"]
    assert grouping in _facets(failed)
    assert failed["status"] == DATADOG_STATUS_CRITICAL
    assert succeeded_report["status"] == DATADOG_STATUS_OK


@pytest.mark.parametrize("environment", DEPLOY_ENVIRONMENTS)
def test_every_monitor_notifies_a_reachable_handle(environment: str) -> None:
    monitors = re.findall(r'resource "datadog_monitor" "(\w+)"', MONITORS[environment].read_text())
    assert monitors
    for monitor in monitors:
        message = _monitor_attribute(monitor, "message", environment)
        assert re.search(r"@(?:slack-[\w-]+|[\w.-]+@[\w.-]+)", message), monitor


@pytest.mark.parametrize("environment", DEPLOY_ENVIRONMENTS)
def test_source_sync_failure_monitor_consumes_the_reported_metric(environment: str) -> None:
    assert _monitor_attribute("source_sync_failed", "query", environment) == (
        f"sum(last_15m):sum:ufo.{SOURCE_SYNC_FAILED_METRIC}{{env:{environment}}} "
        "by {provider,stream}.as_count() >= 1"
    )
    message = _monitor_attribute("source_sync_failed", "message", environment)
    assert "{{provider.name}}" in message
    assert "{{stream.name}}" in message
    assert _monitor_attribute("source_sync_failed", "critical", environment) == "1"
    assert _monitor_attribute("source_sync_failed", "require_full_window", environment) == "false"


def test_edge_doors_use_separate_environment_origins() -> None:
    source = (ROOT / "infra" / "envs" / "edge" / "main.tf").read_text()
    doors = dict(
        re.findall(
            r'module "(?:prod|testing)" \{.*?hostname\s*=\s*"([^"]+)".*?'
            r'origin_base\s*=\s*"([^"]+)"',
            source,
            re.DOTALL,
        )
    )
    assert doors == {
        "flyingobject.ai": "https://origin.flyingobject.ai",
        "testing.flyingobject.ai": "https://origin.testing.flyingobject.ai",
    }
    hosted = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    assert "${apex_host},${gateway_origin_host}" in hosted
    assert "hosts: [${apex_host}, ${gateway_origin_host}]" in hosted
    origin = re.search(
        r"    - host: \$\{gateway_origin_host\}\n.*?(?=\n---)",
        hosted,
        re.DOTALL,
    )
    assert origin
    assert (
        origin.group(0)
        == """    - host: ${gateway_origin_host}
      http:
        paths:
          - path: /ufo
            pathType: Exact
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /fleet
            pathType: Exact
            backend:
              service:
                name: ufo-gateway
                port: {name: http}"""
    )
    for environment in DEPLOY_ENVIRONMENTS:
        ufo = (ROOT / "infra" / "envs" / environment / "ufo.tf").read_text()
        assert 'gateway_origin_host = "origin.${module.platform.hostname}"' in ufo
        assert "gateway_origin_host              = local.gateway_origin_host" in ufo


def test_edge_worker_artifact_substitutes_every_placeholder() -> None:
    module = ROOT / "infra" / "modules" / "edge"
    terraform = (module / "main.tf").read_text()
    assert (
        'landing_html    = replace(file("${path.module}/landing.html"), '
        '"__HOSTNAME__", var.hostname)' in terraform
    )
    assert '"\\"__LANDING_HTML__\\"",\n      jsonencode(local.landing_html)' in terraform
    assert '"\\"__WAITLIST_SENDER__\\"",\n    jsonencode(local.waitlist_sender)' in terraform
    assert (module / "landing.html").read_text().count("__HOSTNAME__") == 5
    worker = (module / "worker.js").read_text()
    assert worker.count('"__LANDING_HTML__"') == 1
    assert worker.count('"__WAITLIST_SENDER__"') == 1
