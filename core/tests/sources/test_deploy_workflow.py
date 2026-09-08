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

from infra.testing_secrets import SECRET_INPUTS
from ufo.db import MAX_OVERFLOW, OWNER_MAX_OVERFLOW, OWNER_POOL_SIZE, POOL_SIZE
from ufo.harness.durability import DBOS_CLIENT_POOL_SIZE
from ufo.product import PRODUCT_CENSUS_SECONDS
from ufo.runtime.jobs import JOB_FAILED_METRIC
from ufo.runtime.sources.sync import SOURCE_SYNC_CHECK
from ufo.schema.records import DBOS_SYSTEM_DATABASE_POOL_SIZE

ROOT = Path(__file__).parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
# Terraform template directive lines, stripped before a template parses as YAML. Every condition
# is stripped rather than a named few: a deploy knob added to the template is not a change to what
# these tests read, and an allowlist made it one — three copies of it, each failing on the next
# knob.
TEMPLATE_DIRECTIVE = r"(?m)^%\{ [^}]*\}\n?"
PRODUCTION_PREREQUISITES = ROOT / ".github" / "scripts" / "production_prerequisites.sh"
AWAIT_ROLLOUT = ".github/scripts/await_rollout.sh"
AWAIT_DRAINED = ".github/scripts/await_drained.sh"
DEPLOY_ENVIRONMENTS = ("testing", "prod")
TESTED_TEMPLATES = "small=ufo-sbx-small:b1,medium=ufo-sbx-medium:b2,large=ufo-sbx-large:b3"
MONITORS = {
    environment: ROOT / "infra" / "envs" / environment / "monitors.tf"
    for environment in DEPLOY_ENVIRONMENTS
}
RUN_URL = "https://github.com/metalcraftai/ufo/actions/runs/30120902872"
DATADOG_STATUS_OK = 0
DATADOG_STATUS_CRITICAL = 2
M6I_XLARGE_DEFAULT_VCPUS = 4
RUNTIME_DATABASE_LOOPS = 3
NON_RUNTIME_DATABASE_CONNECTIONS = 68
TESTING_DATABASE_CONNECTIONS = 397
STORAGE_QUERY = (
    "min(last_30m):avg:aws.rds.free_storage_space{dbinstanceidentifier:"
    "${module.platform.db_instance_identifier}} / avg:aws.rds.total_storage_space"
    "{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 0.05"
)
# A Datadog notification handle in a monitor message: a Slack channel or an email address.
HANDLE = r"@(?:slack-[\w-]+|[\w.-]+@[\w.-]+)"


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


def _step(job: str, name: str, workflow: str = "deploy.yml") -> dict[str, object]:
    jobs = _workflow(WORKFLOWS / workflow)["jobs"]
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
    case "$HOST" in
      *testing*) APP_HOST=app.testing.ufo.ai ;;
      *) APP_HOST=app.ufo.ai ;;
    esac
    [ "$BAD_LOGIN_HOST" != "$HOST" ] || APP_HOST=wrong.example
    printf '%s' "$FORMAT" |
      sed -e 's/%{http_code}/302/g' -e "s|%{redirect_url}|https://$APP_HOST/login|g"
    ;;
  */v1/onboard/ufo)
    [ "$BAD_ONBOARD_HOST" != "$HOST" ] || { printf 'wrong\n'; exit; }
    printf 'x-ufo-session header is required.\n'
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
  */privacy)
    [ "$BAD_LEGAL_PATH" != privacy ] || { printf 'wrong\\n'; exit; }
    printf '<h1>Privacy Policy</h1>\\n'
    ;;
  */terms)
    [ "$BAD_LEGAL_PATH" != terms ] || { printf 'wrong\\n'; exit; }
    printf '<h1>Terms of Service</h1>\\n'
    ;;
  */)
    [ "$BAD_ROOT_HOST" != "$HOST" ] || { printf 'wrong\\n'; exit; }
    printf '  Sign up: https://ufo.ai/join/ufo\\n'
    ;;
esac
"""

KUBECTL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$KUBECTL_CALLS"
case "$*" in
  *"jsonpath={.metadata.generation}") printf '%s\\n' "$KUBECTL_GENERATION" ;;
  *"jsonpath={.status.observedGeneration}") printf '%s\\n' "$KUBECTL_OBSERVED_GENERATION" ;;
  *"/revision}") printf '%s\\n' "$KUBECTL_REVISION" ;;
  *"jsonpath={.spec.replicas}") printf '%s\\n' "$KUBECTL_REPLICAS" ;;
  *"get replicasets -o json") cat "$KUBECTL_REPLICASETS" ;;
  *"get replicaset/ufo-serve-new -o jsonpath={.status.availableReplicas}")
    printf '%s\\n' "$KUBECTL_AVAILABLE" ;;
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
    aws = tmp_path / "aws"
    aws.write_text(AWS_STUB)
    aws.chmod(0o755)
    calls = tmp_path / "aws-calls"
    run = subprocess.run(
        ["bash", str(PRODUCTION_PREREQUISITES)],
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
    tmp_path: Path, testing_result: str, edge_result: str = "success"
) -> tuple[str, dict[str, object]]:
    step = _step("deploy", "Report the deploy conclusion to Datadog")
    environment = step["env"]
    assert isinstance(environment, dict)
    script = step["run"]
    assert isinstance(script, str)

    stubs = tmp_path / f"testing-{testing_result}-{edge_result}"
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
            "EDGE_RESULT": edge_result,
            "RUN_URL": RUN_URL,
        },
    )
    submitted = json.loads(payload.read_text())
    assert isinstance(submitted, list)
    assert len(submitted) == 1
    reported = submitted[0]
    assert isinstance(reported, dict)
    assert "env:testing" in reported["tags"]
    return url.read_text(), reported


def _report_production(
    tmp_path: Path,
    deploy_result: str,
    *,
    ref_result: str = "success",
    validate_result: str = "success",
    prepare_result: str = "success",
) -> tuple[str, dict[str, object]]:
    step = _step(
        "report", "Report the production deploy conclusion to Datadog", "deploy-production.yml"
    )
    assert step["if"] == "always()"
    environment = step["env"]
    assert environment == {
        "DD_API_KEY": "${{ secrets.DD_API_KEY }}",
        "DD_CHECK_URL": "https://api.us5.datadoghq.com/api/v1/check_run",
        "DEPLOY_CHECK": "ufo.deploy.main",
        "DD_STATUS_OK": "0",
        "DD_STATUS_CRITICAL": "2",
        "REF_RESULT": "${{ needs.ref.result }}",
        "VALIDATE_RESULT": "${{ needs.validate.result }}",
        "PREPARE_RESULT": "${{ needs.prepare.result }}",
        "DEPLOY_RESULT": "${{ needs.deploy.result }}",
        "RUN_URL": (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
        ),
    }
    script = step["run"]
    assert isinstance(script, str)
    stubs = tmp_path / (
        f"production-{ref_result}-{validate_result}-{prepare_result}-{deploy_result}"
    )
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
            "REF_RESULT": ref_result,
            "VALIDATE_RESULT": validate_result,
            "PREPARE_RESULT": prepare_result,
            "DEPLOY_RESULT": deploy_result,
            "RUN_URL": RUN_URL,
        },
    )
    submitted = json.loads(payload.read_text())
    assert isinstance(submitted, list)
    assert len(submitted) == 1
    reported = submitted[0]
    assert isinstance(reported, dict)
    assert "env:prod" in reported["tags"]
    return url.read_text(), reported


def _facets(reported: dict[str, object]) -> set[str]:
    tags = reported["tags"]
    assert isinstance(tags, list)
    named = {str(tag).split(":", 1)[0] for tag in tags}
    return named | {"host"} if reported.get("host_name") else named


def _check_platform_addons_wait_for_the_load_balancer_webhook() -> None:
    source = _code((ROOT / "infra" / "modules" / "platform" / "addons.tf").read_text())
    controller = _terraform_block(source, "resource", "aws_load_balancer_controller")
    assert "wait             = true" in controller
    for name in ("cert_manager", "external_secrets", "external_dns"):
        addon = _terraform_block(source, "resource", name)
        assert "depends_on = [helm_release.aws_load_balancer_controller]" in addon
    external_dns = _terraform_block(source, "resource", "external_dns")
    assert "wait             = false" in external_dns
    assert "atomic" not in external_dns


def _check_webhook_addons_install_atomically() -> None:
    source = _code((ROOT / "infra" / "modules" / "platform" / "addons.tf").read_text())
    for name in ("aws_load_balancer_controller", "cert_manager", "external_secrets"):
        addon = _terraform_block(source, "resource", name)
        assert "atomic           = true" in addon


def _check_database_storage_monitor_tracks_allocation() -> None:
    for environment in DEPLOY_ENVIRONMENTS:
        assert _monitor_attribute("db_storage_low", "query", environment) == STORAGE_QUERY
        assert _monitor_attribute("db_storage_low", "critical", environment) == "0.05"
        assert _monitor_attribute("db_storage_low", "warning", environment) == "0.08"
        assert _monitor_attribute("db_storage_low", "evaluation_delay", environment) == "900"


def _check_every_main_push_triggers_deployment() -> None:
    workflow = _workflow(WORKFLOWS / "deploy.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert triggers["push"] == {"branches": ["main"]}

    production = _workflow(WORKFLOWS / "deploy-production.yml")
    production_triggers = production["on"]
    assert isinstance(production_triggers, dict)
    assert set(production_triggers) == {"workflow_dispatch"}
    inputs = production_triggers["workflow_dispatch"]["inputs"]
    assert inputs["target_sha"]["required"] == "true"
    assert inputs["replace_attempt_sha"]["required"] == "false"
    assert inputs["replace_attempt_sha"]["default"] == ""
    assert production["concurrency"] == {
        "group": "deploy-production",
        "cancel-in-progress": "false",
    }
    assert production["env"] == {
        "AWS_REGION": "us-east-1",
        "TF_DIR": "infra/envs/prod",
    }
    assert "concurrency" not in workflow
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    assert "deploy-testing" in str(rollout["concurrency"]["group"])
    assert rollout["concurrency"]["cancel-in-progress"] == "false"


def _check_every_pull_request_has_one_deployment_gate() -> None:
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


def test_manual_production_target_must_have_a_successful_testing_run(tmp_path: Path) -> None:
    script = _step("prepare", "Select tested deployment", "deploy-production.yml")["run"]
    assert isinstance(script, str)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-m",
            "seed",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    target_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-m",
            "newer",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    main_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", main_sha],
        cwd=repo,
        check=True,
    )
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" > "$GH_ARGS"\n'
        'test -n "$TESTING_RUN_ID" && printf \'%s %s %s\\n\' "$TESTING_RUN_ID" '
        '"$TESTED_STATUS" "$TESTED_CONCLUSION"\n'
    )
    gh.chmod(0o755)

    def select(
        candidate: str,
        testing_run_id: str = "42",
        tested_status: str = "completed",
        tested_conclusion: str = "success",
    ) -> subprocess.CompletedProcess[bytes]:
        output = tmp_path / "github-output"
        output.write_text("")
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=repo,
            capture_output=True,
            env={
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "metalcraftai/ufo",
                "GH_ARGS": str(tmp_path / "gh-args"),
                "TARGET_SHA": candidate,
                "TESTED_STATUS": tested_status,
                "TESTED_CONCLUSION": tested_conclusion,
                "TESTING_RUN_ID": testing_run_id,
            },
        )

    accepted = select(target_sha)
    assert accepted.returncode == 0, accepted.stderr.decode()
    assert (tmp_path / "github-output").read_text().splitlines() == [
        f"target_sha={target_sha}",
        "testing_run_id=42",
    ]
    assert select(target_sha[:8]).returncode != 0
    assert select(target_sha.upper()).returncode != 0
    assert select("0" * 40).returncode != 0
    assert select(target_sha, testing_run_id="").returncode != 0
    assert select(target_sha, tested_status="in_progress", tested_conclusion="").returncode != 0
    assert select(target_sha, tested_conclusion="failure").returncode != 0
    query = (tmp_path / "gh-args").read_text()
    assert "event=push" in query
    assert f"head_sha={target_sha}" in query
    assert "per_page=1" in query
    assert "status=" not in query


def _check_pull_request_plans_active_deployment_inputs() -> None:
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
        r"^(\.github/(workflows/deploy(-production)?\.yml|"
        r"scripts/(billing_export_plan_(check\.py|gate\.sh)|deploy_change_gate\.py|"
        r"terraform_plan_guard\.py|"
        r"production_prerequisites\.sh))$|"
        r"infra/(production_secrets|testing_secrets)\.py$|"
        r"infra/(production-access|envs/(testing|prod|edge)|modules/(platform|edge)|templates)/)"
    )
    script = selector["run"]
    assert isinstance(script, str)
    assert 'git diff --name-only --no-renames "$BASE_SHA...$HEAD_SHA"' in script
    assert (
        'git diff --no-renames "$BASE_SHA...$HEAD_SHA" -- '
        "infra/modules/platform/iam.tf infra/modules/platform/ses.tf" in script
    )
    assert (
        'python .github/scripts/deploy_change_gate.py "$RUNNER_TEMP/deploy-paths" '
        '"$RUNNER_TEMP/auth-diff"' in script
    )
    assert 'if [ "$GITHUB_EVENT_NAME" != "pull_request" ]' in script
    assert "exit 0" not in script


def _check_testing_deploy_writes_provider_credentials_before_apply() -> None:
    workflow = _workflow(WORKFLOWS / "deploy.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    assert "environment" not in rollout
    steps = rollout["steps"]
    assert isinstance(steps, list)
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    write = _step("rollout", "Write testing runtime secrets")
    assert write == {
        "name": "Write testing runtime secrets",
        "if": "github.event_name != 'pull_request'",
        "env": {
            "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
            "PERPLEXITY_API_KEY": "${{ secrets.PERPLEXITY_API_KEY }}",
            "SPECTRUM_PROJECT_ID": "${{ secrets.TESTING_SPECTRUM_PROJECT_ID }}",
            "SPECTRUM_PROJECT_SECRET": "${{ secrets.TESTING_SPECTRUM_PROJECT_SECRET }}",
            "TESTING_API_KEYS_SECRET_ID": "ufo/ufo-testing/api-keys",
            "TESTING_DEPLOYMENT_ID": "${{ github.run_id }}-${{ github.run_attempt }}",
        },
        "run": "uv run python infra/testing_secrets.py",
    }
    # Every input the script requires is passed here, or the step fails on the first deploy after it
    # is declared.
    assert set(SECRET_INPUTS.values()) <= set(write["env"])
    assert names.index("Terraform init") < names.index("Write testing runtime secrets")
    assert names.index("Write testing runtime secrets") < names.index("Terraform apply")

    refresh = _step("rollout", "Refresh testing runtime secrets")
    assert refresh == {
        "name": "Refresh testing runtime secrets",
        "if": "github.event_name != 'pull_request'",
        "run": (
            "aws eks update-kubeconfig \\\n"
            '  --region "$AWS_REGION" \\\n'
            '  --name "$(terraform -chdir="$TF_DIR" output -raw cluster_name)"\n'
            'NAMESPACE="$(terraform -chdir="$TF_DIR" show -json '
            '"$RUNNER_TEMP/testing.tfplan" \\\n'
            "  | jq -er '.planned_values.outputs.system_namespace.value')\"\n"
            "python infra/secret_sync.py \\\n"
            '  "$NAMESPACE" "$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"\n'
        ),
    }
    # The write publishes a new Secrets Manager version; the projection carries the previous one
    # until the controller republishes it. Force the sync and compare bytes before the apply rolls
    # pods, or they read the superseded value (outage 0002).
    assert names.index("Write testing runtime secrets") < names.index(
        "Refresh testing runtime secrets"
    )
    assert names.index("Refresh testing runtime secrets") < names.index("Terraform apply")


def _check_testing_deploy_seeds_metric_names_before_apply() -> None:
    """Datadog refuses a tag configuration for a metric it holds no point for, so the apply that
    ships a new distribution's emitter cannot also configure it — and the refusal fails the whole
    root, taking every deploy queued behind this one with it."""
    names = [
        step.get("name")
        for step in _workflow(WORKFLOWS / "deploy.yml")["jobs"]["rollout"]["steps"]
        if isinstance(step, dict)
    ]
    assert _step("rollout", "Seed new metric names") == {
        "name": "Seed new metric names",
        "if": "github.event_name != 'pull_request'",
        "run": "uv run python infra/seed_metrics.py",
    }
    assert names.index("Seed new metric names") < names.index("Terraform apply")


_ADDITIVE_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    '@@ -0,0 +1,2 @@\n+resource "aws_iam_policy" "cache_s3" {\n+}\n'
)
_CONTRACTION_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    '@@ -1,3 +1,1 @@\n-  actions = ["s3:GetObject", "s3:PutObject"]\n'
)
_COMMENT_ONLY_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    "@@ -1,2 +1,1 @@\n-  # a stale comment\n"
)
_NARROWING_ARGUMENT_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    '@@ -63,6 +63,7 @@ module "irsa_app_s3" {\n'
    "   role_policy_arns = { s3 = aws_iam_policy.app_s3.arn }\n"
    '+  assume_role_condition_test = "StringEquals"\n'
)
_NARROWING_BLOCK_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    '@@ -45,6 +45,10 @@ data "aws_iam_policy_document" "app_s3" {\n'
    "   statement {\n"
    '+    condition {\n+      test     = "StringNotEquals"\n'
    '+      variable = "aws:PrincipalTag/env"\n+    }\n'
)
_DENY_ATTACHMENT_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    '@@ -112,3 +112,20 @@ module "irsa_cache_s3" {\n'
    "   tags = local.tags\n }\n"
    '+data "aws_iam_policy_document" "app_s3_no_delete" {\n'
    "+  statement {\n"
    '+    effect    = "Deny"\n'
    '+    actions   = ["s3:DeleteObject"]\n'
    '+    resources = ["${aws_s3_bucket.blob.arn}/*"]\n'
    "+  }\n+}\n"
    '+resource "aws_iam_policy" "app_s3_no_delete" {\n'
    "+  policy = data.aws_iam_policy_document.app_s3_no_delete.json\n+}\n"
    '+resource "aws_iam_role_policy_attachment" "app_s3_no_delete" {\n'
    "+  role       = module.irsa_app_s3.iam_role_name\n"
    "+  policy_arn = aws_iam_policy.app_s3_no_delete.arn\n+}\n"
)
_LIVE_ROLE_ATTACHMENT_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    '@@ -112,3 +112,6 @@ module "irsa_cache_s3" {\n'
    "   tags = local.tags\n }\n"
    '+resource "aws_iam_role_policy_attachment" "app_s3_scoped" {\n'
    "+  role       = module.irsa_app_s3.iam_role_name\n"
    "+  policy_arn = aws_iam_policy.app_s3_scoped.arn\n+}\n"
)
_EXPANSION_BLOCK_GROUP_AUTH_DIFF = (
    "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
    '@@ -72,3 +72,24 @@ module "irsa_app_s3" {\n'
    "   tags = local.tags\n }\n"
    '+data "aws_iam_policy_document" "cache_s3" {\n'
    "+  statement {\n"
    '+    actions   = ["s3:GetObject", "s3:PutObject"]\n'
    '+    resources = ["${aws_s3_bucket.cache.arn}/*"]\n'
    "+  }\n+}\n"
    '+resource "aws_iam_policy" "cache_s3" {\n'
    "+  policy = data.aws_iam_policy_document.cache_s3.json\n+}\n"
    '+module "irsa_cache_s3" {\n'
    "+  role_name        = local.cache_s3_role_name\n"
    "+  role_policy_arns = { s3 = aws_iam_policy.cache_s3.arn }\n"
    "+  oidc_providers = {\n"
    "+    main = {\n"
    "+      provider_arn = module.eks.oidc_provider_arn\n"
    "+    }\n+  }\n+}\n"
    '+resource "aws_iam_role_policy_attachment" "cache_s3" {\n'
    "+  role       = module.irsa_cache_s3.iam_role_name\n"
    "+  policy_arn = aws_iam_policy.cache_s3.arn\n+}\n"
)


def _check_authorization_expansions_co_deploy_but_contractions_split() -> None:
    gate = _deploy_change_gate()
    # Authorization alone, or a runtime change alone, is always fine.
    gate.validate_deploy_change(("infra/modules/platform/iam.tf",), _ADDITIVE_AUTH_DIFF)
    gate.validate_deploy_change(("core/src/ufo/serve.py",))
    gate.validate_deploy_change(
        ("infra/modules/platform/iam.tf", "core/tests/test_deploy_workflow.py"),
        _ADDITIVE_AUTH_DIFF,
    )
    # A whole new role, its policy and the attachment that binds them co-deploy: no live principal
    # holds the new grant, so the roll cannot break on it.
    gate.validate_deploy_change(
        ("infra/modules/platform/iam.tf", "core/src/ufo/serve.py"),
        _EXPANSION_BLOCK_GROUP_AUTH_DIFF,
    )
    # A new grant (expansion) co-deploys with its consumer — terraform creates it before the roll.
    for runtime_path in (
        "infra/envs/testing/ufo.tf",
        "infra/templates/hosted.yaml.tpl",
        "core/src/ufo/serve.py",
    ):
        gate.validate_deploy_change(
            ("infra/modules/platform/iam.tf", runtime_path), _ADDITIVE_AUTH_DIFF
        )
    # Removing a grant (contraction) must split from its consumers.
    with pytest.raises(ValueError, match="contract IAM only after"):
        gate.validate_deploy_change(
            ("infra/modules/platform/iam.tf", "infra/templates/hosted.yaml.tpl"),
            _CONTRACTION_AUTH_DIFF,
        )
    # A comment/blank-only removal changes no grant, so it is not a contraction.
    gate.validate_deploy_change(
        ("infra/modules/platform/iam.tf", "infra/envs/prod/ufo.tf"), _COMMENT_ONLY_AUTH_DIFF
    )
    # Added lines narrow an existing grant as surely as removed lines drop it: an argument added
    # to a live role, or a condition added to a live statement, is a contraction too.
    # Whole new blocks narrow too: a Deny denies whoever holds the policy, and an attachment onto a
    # role the diff does not create rewrites the authorization of a principal already live.
    for narrowing_diff in (
        _NARROWING_ARGUMENT_AUTH_DIFF,
        _NARROWING_BLOCK_AUTH_DIFF,
        _DENY_ATTACHMENT_AUTH_DIFF,
        _LIVE_ROLE_ATTACHMENT_AUTH_DIFF,
    ):
        with pytest.raises(ValueError, match="contract IAM only after"):
            gate.validate_deploy_change(
                ("infra/modules/platform/iam.tf", "core/src/ufo/serve.py"), narrowing_diff
            )
    # Fail closed: no diff supplied means expand-vs-contract is unknown, so enforce the split.
    with pytest.raises(ValueError, match="contract IAM only after"):
        gate.validate_deploy_change(
            ("infra/modules/platform/ses.tf", "servers/control/src/email.rs")
        )


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

    def commit_content(files: dict[str, str], message: str) -> str:
        for path, content in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        git("add", "-A")
        git("commit", "-m", message)
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

    # Put existing grants on main so a later diff can add beside them, narrow one, or remove one.
    git("checkout", "main")
    keep_grant = 'module "keep" {\n  role_policy_arns = { s3 = "arn" }\n}\n'
    drop_grant = 'module "drop" {\n  role_policy_arns = { s3 = "arn" }\n}\n'
    iam_base = commit_content(
        {"infra/modules/platform/iam.tf": keep_grant + drop_grant}, "iam-base"
    )
    git("update-ref", "refs/remotes/origin/main", iam_base)

    # Expansion: a new grant added to iam.tf beside a runtime change co-deploys — gate allows it.
    git("checkout", "-b", "expand")
    expand_head = commit_content(
        {
            "infra/modules/platform/iam.tf": keep_grant
            + drop_grant
            + 'module "new" {\n  role_policy_arns = { s3 = "arn" }\n}\n',
            "core/src/ufo/runtime/engine.py": "x = 1\n",
        },
        "expand",
    )
    code, output, stderr = run_select("workflow_dispatch", "origin/main", expand_head)
    assert code == 0, stderr
    assert "deploy=true" in output

    # Contraction: removing a grant beside a runtime change must split across deploys — rejected.
    git("checkout", "main")
    git("checkout", "-b", "contract")
    contract_head = commit_content(
        {
            "infra/modules/platform/iam.tf": keep_grant,
            "core/src/ufo/runtime/engine.py": "x = 1\n",
        },
        "contract",
    )
    code, output, stderr = run_select("workflow_dispatch", "origin/main", contract_head)
    assert code == 1
    assert "contract IAM only after" in stderr
    assert "deploy=" not in output

    # Contraction by addition: one line added inside a live role narrows it — also rejected.
    git("checkout", "main")
    git("checkout", "-b", "narrow")
    narrow_head = commit_content(
        {
            "infra/modules/platform/iam.tf": 'module "keep" {\n'
            '  assume_role_condition_test = "StringEquals"\n'
            '  role_policy_arns = { s3 = "arn" }\n}\n' + drop_grant,
            "core/src/ufo/runtime/engine.py": "x = 1\n",
        },
        "narrow",
    )
    code, output, stderr = run_select("workflow_dispatch", "origin/main", narrow_head)
    assert code == 1
    assert "contract IAM only after" in stderr
    assert "deploy=" not in output

    # Contraction by whole new blocks: a Deny bound to a role that already exists narrows that
    # live role while its consumer rolls — rejected even though the diff only adds.
    git("checkout", "main")
    git("checkout", "-b", "deny")
    deny_head = commit_content(
        {
            "infra/modules/platform/iam.tf": keep_grant
            + drop_grant
            + 'data "aws_iam_policy_document" "no_delete" {\n'
            '  statement {\n    effect = "Deny"\n  }\n}\n'
            'resource "aws_iam_role_policy_attachment" "no_delete" {\n'
            "  role = module.keep.iam_role_name\n}\n",
            "core/src/ufo/runtime/engine.py": "x = 1\n",
        },
        "deny",
    )
    code, output, stderr = run_select("workflow_dispatch", "origin/main", deny_head)
    assert code == 1
    assert "contract IAM only after" in stderr
    assert "deploy=" not in output


def test_deploy_change_gate_entrypoint_exits_nonzero_on_the_boundary(tmp_path: Path) -> None:
    """The gate's CI contract is its exit code: the argv-read → validate → exit path CI actually
    invokes, not the imported function."""
    gate = ROOT / ".github" / "scripts" / "deploy_change_gate.py"
    paths = tmp_path / "deploy-paths"
    paths.write_text("infra/modules/platform/iam.tf\ncore/src/ufo/serve.py\n")
    # No auth diff supplied: expand-vs-contract is unknown, so the gate fails closed.
    rejected = subprocess.run(
        [sys.executable, str(gate), str(paths)], capture_output=True, text=True
    )
    assert rejected.returncode == 1
    assert "contract IAM only after" in rejected.stderr

    # A contraction diff is rejected even with the diff present.
    auth_diff = tmp_path / "auth-diff"
    auth_diff.write_text(
        "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
        '@@ -1,2 +1,1 @@\n-  actions = ["s3:PutObject"]\n'
    )
    contracted = subprocess.run(
        [sys.executable, str(gate), str(paths), str(auth_diff)], capture_output=True, text=True
    )
    assert contracted.returncode == 1
    assert "contract IAM only after" in contracted.stderr

    # An additive diff (a new grant) co-deploys with its consumer.
    auth_diff.write_text(
        "--- a/infra/modules/platform/iam.tf\n+++ b/infra/modules/platform/iam.tf\n"
        '@@ -0,0 +1,1 @@\n+resource "aws_iam_policy" "cache_s3" {}\n'
    )
    expanded = subprocess.run(
        [sys.executable, str(gate), str(paths), str(auth_diff)], capture_output=True, text=True
    )
    assert expanded.returncode == 0, expanded.stderr

    paths.write_text("core/src/ufo/serve.py\n")
    allowed = subprocess.run(
        [sys.executable, str(gate), str(paths)], capture_output=True, text=True
    )
    assert allowed.returncode == 0
    assert allowed.stderr == ""


def _check_plans_run_only_for_selected_deployment_inputs() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    client = jobs["client"]
    assert isinstance(client, dict)
    assert client["needs"] == "changes"
    assert client["if"] == "needs.changes.outputs.client_build == 'true'"

    images = jobs["images"]
    assert isinstance(images, dict)
    assert images["needs"] == "changes"
    assert images["if"] == (
        "needs.changes.outputs.deploy == 'true' && github.event_name != 'pull_request'"
    )
    bundle = jobs["bundle"]
    assert isinstance(bundle, dict)
    assert bundle["needs"] == ["changes", "client"]
    assert bundle["if"] == (
        "${{ !cancelled() && needs.changes.outputs.deploy == 'true' && "
        "github.event_name != 'pull_request' && "
        "(needs.client.result == 'success' || needs.client.result == 'skipped') }}"
    )
    gateway = jobs["gateway"]
    assert isinstance(gateway, dict)
    assert gateway["needs"] == ["changes", "client"]
    assert gateway["if"] == (
        "${{ !cancelled() && needs.changes.outputs.deploy == 'true' && "
        "github.event_name != 'pull_request' && "
        "(needs.client.result == 'success' || needs.client.result == 'skipped') }}"
    )

    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    assert rollout["needs"] == ["changes", "client", "bundle", "images", "gateway"]
    assert rollout["if"] == (
        "${{ !cancelled() && needs.changes.outputs.deploy == 'true' && "
        "(needs.client.result == 'success' || needs.client.result == 'skipped') && "
        "(needs.bundle.result == 'success' || needs.bundle.result == 'skipped') && "
        "(needs.images.result == 'success' || needs.images.result == 'skipped') && "
        "(needs.gateway.result == 'success' || needs.gateway.result == 'skipped') }}"
    )

    edge = jobs["edge"]
    assert isinstance(edge, dict)
    assert edge["needs"] == ["changes", "rollout"]
    # `client` skips whenever the client tree already built, and GitHub skips a descendant of a
    # skipped job unless it opts out. Without `!cancelled()` the edge plan skips on every such run
    # and the deployment gate, which demands it succeeded, fails the whole deploy.
    assert edge["if"] == (
        "${{ !cancelled() && needs.changes.outputs.deploy == 'true' && "
        "needs.rollout.result == 'success' }}"
    )

    production = jobs["production"]
    assert isinstance(production, dict)
    assert production["needs"] == "changes"
    assert production["if"] == (
        "github.event_name == 'pull_request' && needs.changes.outputs.deploy == 'true'"
    )
    production_access = jobs["production_access"]
    assert isinstance(production_access, dict)
    assert production_access["needs"] == "changes"
    assert production_access["if"] == (
        "github.event_name == 'pull_request' && needs.changes.outputs.deploy == 'true'"
    )
    assert "production_review" not in jobs
    assert "production_deploy" not in jobs
    # No job here declares an environment, so every `secrets.*` this workflow reads resolves to the
    # repository value — testing's. `ANTHROPIC_API_KEY` is one name holding two credentials: the
    # repository secret keys the testing fleet and the nightly evals, and the `production`
    # environment secret shadows it for the production deploy alone. An environment declared on any
    # job here would hand production's key to the testing fleet.
    assert not any(isinstance(job, dict) and "environment" in job for job in jobs.values())
    source = (WORKFLOWS / "deploy.yml").read_text()
    for secret in (
        "BROWSERBASE_API_KEY",
        "TURBOPUFFER_API_KEY",
    ):
        assert secret not in source
    assert "github-production-deploy" not in source
    for job in jobs.values():
        assert isinstance(job, dict)
        job_environment = job.get("env", {})
        assert isinstance(job_environment, dict)
        for step in job.get("steps", []):
            if isinstance(step, dict) and step.get("name") == "Terraform apply":
                assert job_environment.get("TF_DIR") != "infra/envs/prod"
                assert step.get("working-directory") != "infra/production-access"


def test_client_builds_are_skipped_for_a_pushed_client_tree(tmp_path: Path) -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    changes = jobs["changes"]
    assert isinstance(changes, dict)
    assert changes["outputs"] == {
        "deploy": "${{ steps.select.outputs.deploy }}",
        "image_tag": "${{ steps.tag.outputs.tag }}",
        "client_build": "${{ steps.client.outputs.build }}",
        "client_tree": "${{ steps.client.outputs.tree }}",
    }
    changes_steps = changes["steps"]
    assert isinstance(changes_steps, list)
    credentials = next(
        step
        for step in changes_steps
        if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
    )
    assert credentials["if"] == "steps.select.outputs.deploy == 'true'"
    probe = _step("changes", "Select client work")
    assert probe["if"] == "steps.select.outputs.deploy == 'true'"
    assert changes_steps.index(credentials) < changes_steps.index(probe)

    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    rollout_steps = rollout["steps"]
    assert isinstance(rollout_steps, list)
    download = next(
        step for step in rollout_steps if step.get("uses") == "actions/download-artifact@v4"
    )
    assert download["if"] == (
        "github.event_name != 'pull_request' && needs.client.result == 'success'"
    )
    assert download["with"]["path"] == "servers/control/clientbin"
    push = _step("rollout", "Push the client binaries for this tree")
    assert push["if"] == "github.event_name != 'pull_request' && needs.client.result == 'success'"
    assert push["env"] == {"CLIENT_TREE": "${{ needs.changes.outputs.client_tree }}"}
    assert 'docker push "$ECR_REGISTRY/ufo-clientbin:$CLIENT_TREE"' in push["run"]
    apply = _step("rollout", "Terraform apply")
    assert rollout_steps.index(apply) < rollout_steps.index(push)

    gateway = jobs["gateway"]
    assert isinstance(gateway, dict)
    assert gateway["env"] == {
        "CLIENT_TREE": "${{ needs.changes.outputs.client_tree }}",
        "IMAGE_TAG": "${{ needs.changes.outputs.image_tag }}",
    }
    gateway_steps = gateway["steps"]
    assert isinstance(gateway_steps, list)
    gateway_download = next(
        step for step in gateway_steps if step.get("uses") == "actions/download-artifact@v4"
    )
    assert gateway_download["if"] == (
        "steps.select.outputs.work == 'build' && needs.client.result == 'success'"
    )
    assert gateway_download["with"]["path"] == "servers/control/clientbin"
    reuse = _step("gateway", "Reuse the pushed client binaries")
    assert reuse["if"] == "steps.select.outputs.work == 'build' && needs.client.result == 'skipped'"
    assert "docker cp clientbin:/clientbin/. servers/control/clientbin" in reuse["run"]
    build = next(
        step for step in gateway_steps if step.get("uses") == "docker/build-push-action@v6"
    )
    assert build["with"]["context"] == "servers/control"
    assert (
        gateway_steps.index(gateway_download)
        < gateway_steps.index(reuse)
        < gateway_steps.index(build)
    )

    script = probe["run"]
    assert isinstance(script, str)
    aws = tmp_path / "aws"
    aws.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$AWS_CALLS"\nexit "$AWS_DESCRIBE_EXIT"\n')
    aws.chmod(0o755)

    def select(describe_exit: str, cwd: Path) -> tuple[str, list[str]]:
        output = tmp_path / "github-output"
        output.write_text("")
        calls = tmp_path / "aws-calls"
        calls.write_text("")
        subprocess.run(
            ["bash", "-e", "-c", script],
            cwd=cwd,
            check=True,
            capture_output=True,
            env={
                "HOME": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output),
                "AWS_CALLS": str(calls),
                "AWS_DESCRIBE_EXIT": describe_exit,
            },
        )
        prefix = "ecr describe-images --repository-name ufo-clientbin --image-ids imageTag="
        call = calls.read_text().splitlines()
        assert len(call) == 1
        assert call[0].startswith(prefix)
        return call[0].removeprefix(prefix), output.read_text().splitlines()

    listing = subprocess.run(
        ["git", "ls-tree", "HEAD", "--", "client"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert listing[:2] == ["040000", "tree"]
    tree = listing[2]
    assert select("0", ROOT) == (tree, [f"tree={tree}", "build=false"])
    assert select("1", ROOT) == (tree, [f"tree={tree}", "build=true"])

    scratch = tmp_path / "scratch"
    (scratch / "client").mkdir(parents=True)
    git = ["git", "-c", "user.email=deploy@example.com", "-c", "user.name=deploy"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=scratch, check=True)

    def tag_of(source: str) -> str:
        (scratch / "client" / "main.rs").write_text(source)
        subprocess.run([*git, "add", "-A"], cwd=scratch, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "client"], cwd=scratch, check=True)
        return select("1", scratch)[0]

    first = tag_of("fn main() {}\n")
    assert tag_of("fn main() { install() }\n") != first
    assert tag_of("fn main() {}\n") == first


def test_service_images_skip_and_retag_by_tree(tmp_path: Path) -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    images = jobs["images"]
    assert isinstance(images, dict)
    assert images["strategy"]["matrix"]["include"] == [
        {"repository": "ufo-cache", "dir": "servers/cache"},
        {"repository": "ufo-egress", "dir": "servers/egress"},
        {"repository": "ufo-preview", "dir": "servers/preview"},
    ]
    build = next(
        step for step in images["steps"] if step.get("uses") == "docker/build-push-action@v6"
    )
    assert build["if"] == "steps.select.outputs.work == 'build'"
    assert build["with"]["provenance"] == "false"
    assert build["with"]["tags"] == (
        "${{ env.ECR_REGISTRY }}/${{ matrix.repository }}:${{ env.IMAGE_TAG }}"
    )
    retag = _step("images", "Retag the built tree")
    assert retag["if"] == "steps.select.outputs.work == 'retag'"

    select = _step("images", "Select image work")
    script = select["run"]
    assert isinstance(script, str)
    aws = tmp_path / "aws"
    aws.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *describe-repositories*) exit "$REPO_EXIT" ;;\n'
        '  *"imageTag=$IMAGE_TAG"*) exit "$TAG_EXIT" ;;\n'
        '  *"imageTag=$TREE"*) exit "$TREE_EXIT" ;;\n'
        "  *) exit 43 ;;\n"
        "esac\n"
    )
    aws.chmod(0o755)
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD:servers/cache"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def select_work(repo_exit: str, tag_exit: str, tree_exit: str) -> list[str]:
        output = tmp_path / "github-output"
        output.write_text("")
        subprocess.run(
            ["bash", "-e", "-c", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            env={
                "HOME": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output),
                "REPOSITORY": "ufo-cache",
                "DIR": "servers/cache",
                "IMAGE_TAG": "shortsha0",
                "TREE": tree,
                "REPO_EXIT": repo_exit,
                "TAG_EXIT": tag_exit,
                "TREE_EXIT": tree_exit,
            },
        )
        return output.read_text().splitlines()

    assert select_work("1", "0", "0") == [f"tree={tree}", "work=none"]
    assert select_work("0", "0", "1") == [f"tree={tree}", "work=none"]
    assert select_work("0", "1", "0") == [f"tree={tree}", "work=retag"]
    assert select_work("0", "1", "1") == [f"tree={tree}", "work=build"]

    tag = _step("images", "Tag the built tree")
    assert tag["if"] == "steps.select.outputs.work == 'build'"
    tag_script = tag["run"]
    assert isinstance(tag_script, str)
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'if [ -n "$DOCKER_ERROR" ]; then printf \'%s\\n\' "$DOCKER_ERROR" >&2; exit 1; fi\n'
    )
    docker.chmod(0o755)

    def tag_tree(error: str) -> int:
        return subprocess.run(
            ["bash", "-e", "-c", tag_script],
            capture_output=True,
            env={
                "HOME": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "ECR_REGISTRY": "registry.example",
                "REPOSITORY": "ufo-cache",
                "IMAGE_TAG": "shortsha0",
                "TREE": tree,
                "DOCKER_ERROR": error,
            },
        ).returncode

    assert tag_tree("") == 0
    assert (
        tag_tree(
            "tag invalid: The image tag already exists in the repository "
            "and cannot be overwritten because the repository is immutable"
        )
        == 0
    )
    assert tag_tree("denied: not authorized") != 0


@pytest.mark.parametrize(
    (
        "workflow",
        "job_name",
        "targets",
        "flag_token",
        "plan_name",
        "guard_name",
        "apply_name",
        "gate_name",
        "host",
        "init_step_name",
        "plan_step_name",
        "condition",
        "bindings_target",
        "bindings_plan_name",
        "bindings_step_names",
    ),
    [
        (
            "deploy.yml",
            "edge",
            (
                "cloudflare_ruleset.shipped_app_cache",
                "cloudflare_flagship_flag.testing_portal",
                "module.testing",
            ),
            "flagship_testing_api_token",
            "edge",
            "Reject destructive changes",
            "Terraform apply",
            "Gate testing door",
            "testing.ufo.ai",
            "Terraform init",
            "Terraform plan",
            "github.event_name != 'pull_request'",
            "module.testing.cloudflare_workers_script.edge",
            "edge-bindings",
            (
                "Terraform bindings plan",
                "Reject destructive binding changes",
                "Terraform bindings apply",
            ),
        ),
        (
            "deploy-production.yml",
            "deploy",
            ("cloudflare_flagship_flag.prod_portal", "module.prod"),
            "flagship_prod_api_token",
            "production-edge",
            "Reject destructive production edge changes",
            "Terraform production edge apply",
            "Gate production door",
            "ufo.ai",
            "Terraform production edge init",
            "Terraform production edge plan",
            None,
            "module.prod.cloudflare_workers_script.edge",
            "production-edge-bindings",
            (
                "Terraform production bindings plan",
                "Reject destructive production binding changes",
                "Terraform production bindings apply",
            ),
        ),
    ],
)
def test_edge_deploys_are_isolated(
    tmp_path: Path,
    workflow: str,
    job_name: str,
    targets: tuple[str, ...],
    flag_token: str,
    plan_name: str,
    guard_name: str,
    apply_name: str,
    gate_name: str,
    host: str,
    init_step_name: str,
    plan_step_name: str,
    condition: str | None,
    bindings_target: str,
    bindings_plan_name: str,
    bindings_step_names: tuple[str, str, str],
) -> None:
    jobs = _workflow(WORKFLOWS / workflow)["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    init = _step(job_name, init_step_name, workflow)
    plan = _step(job_name, plan_step_name, workflow)
    guard = _step(job_name, guard_name, workflow)
    apply = _step(job_name, apply_name, workflow)
    gate = _step(job_name, gate_name, workflow)
    plan_path = f"$RUNNER_TEMP/{plan_name}.tfplan"
    assert init["working-directory"] == "infra/envs/edge"
    assert plan["working-directory"] == "infra/envs/edge"
    assert guard["working-directory"] == "infra/envs/edge"
    assert apply["working-directory"] == "infra/envs/edge"
    # One deploy carries one environment's flag credential. The other environment's token is not in
    # this step at all, so a testing deploy — and every pull request, which reaches no environment
    # secret — cannot move a production feature whatever it targets.
    assert plan["env"] == {
        "TF_VAR_cloudflare_api_token": "${{ secrets.CLOUDFLARE_API_TOKEN }}",
        f"TF_VAR_{flag_token}": "${{ secrets." + flag_token.upper() + " }}",
    }
    assert "-lock-timeout=10m" in plan["run"]
    assert tuple(re.findall(r"-target=(\S+)", plan["run"])) == targets
    assert f'-out="{plan_path}"' in plan["run"]
    assert guard["run"] == (
        f'terraform show -json "{plan_path}" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert apply["run"] == f'terraform apply -input=false "{plan_path}"'
    # Cloudflare refuses to delete a queue or a database a Worker still binds, and a release that
    # drops the binding and the resource together leaves the graph no reference to order on, so the
    # module's plan schedules that delete against the live script. The script settles in an apply of
    # its own first, carrying the resources it binds with it, and on the account token alone: the
    # plan reaches no flag, so neither environment's Flagship token is in the step.
    bindings_plan = _step(job_name, bindings_step_names[0], workflow)
    bindings_guard = _step(job_name, bindings_step_names[1], workflow)
    bindings_apply = _step(job_name, bindings_step_names[2], workflow)
    bindings_path = f"$RUNNER_TEMP/{bindings_plan_name}.tfplan"
    assert bindings_plan["working-directory"] == "infra/envs/edge"
    assert bindings_guard["working-directory"] == "infra/envs/edge"
    assert bindings_apply["working-directory"] == "infra/envs/edge"
    assert bindings_plan["env"] == {
        "TF_VAR_cloudflare_api_token": "${{ secrets.CLOUDFLARE_API_TOKEN }}"
    }
    assert "-lock-timeout=10m" in bindings_plan["run"]
    assert tuple(re.findall(r"-target=(\S+)", bindings_plan["run"])) == (bindings_target,)
    assert f'-out="{bindings_path}"' in bindings_plan["run"]
    assert bindings_guard["run"] == (
        f'terraform show -json "{bindings_path}" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert bindings_apply["run"] == f'terraform apply -input=false "{bindings_path}"'
    assert bindings_apply.get("if") == condition
    assert steps.index(bindings_plan) < steps.index(bindings_guard) < steps.index(bindings_apply)
    assert steps.index(bindings_apply) < steps.index(plan)
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
        "BAD_LEGAL_PATH": "",
        "BAD_LOGIN_HOST": "",
        "BAD_ONBOARD_HOST": "",
        "BAD_ROOT_HOST": "",
        "NEGATIVE_FLEET_HOST": "",
        "DOOR_CALLS": str(calls),
    }
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], check=True, env=environment)
    invoked = calls.read_text().splitlines()
    assert len(invoked) == 7
    assert [call.rsplit(" ", 1)[-1] for call in invoked] == [
        f"https://{host}/",
        f"https://{host}/login",
        f"https://{host}/v1/onboard/ufo",
        f"https://{host}/ufo",
        f"https://{host}/fleet",
        f"https://{host}/privacy",
        f"https://{host}/terms",
    ]
    for page in ("privacy", "terms"):
        environment["BAD_LEGAL_PATH"] = page
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            capture_output=True,
            env=environment,
        )
        assert failed.returncode != 0
    environment["BAD_LEGAL_PATH"] = ""
    environment["BAD_ROOT_HOST"] = host
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        env=environment,
    )
    assert failed.returncode != 0
    environment["BAD_ROOT_HOST"] = ""
    environment["BAD_LOGIN_HOST"] = host
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        env=environment,
    )
    assert failed.returncode != 0
    environment["BAD_LOGIN_HOST"] = ""
    environment["BAD_ONBOARD_HOST"] = host
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        env=environment,
    )
    assert failed.returncode != 0
    environment["BAD_ONBOARD_HOST"] = ""
    environment["BAD_HOST"] = host
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        env=environment,
    )
    assert failed.returncode != 0
    environment["BAD_HOST"] = ""
    environment["NEGATIVE_FLEET_HOST"] = host
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        env=environment,
    )
    assert failed.returncode != 0
    environment["NEGATIVE_FLEET_HOST"] = ""
    environment["BAD_FLEET_HOST"] = host
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        env=environment,
    )
    assert failed.returncode != 0


def _check_flag_resources_keep_their_refresh_address() -> None:
    for name in ["testing_portal", "prod_portal"]:
        source = _code((ROOT / "infra" / "envs" / "edge" / "flags.tf").read_text())
        resource = _terraform_block(source, "resource", name)
        assert re.search(r"^\s*key\s*=\s*each\.key$", resource, re.MULTILINE)
        assert re.search(r"^\s*flag_key\s*=\s*each\.key$", resource, re.MULTILINE)


def _check_production_edge_preserves_the_promoted_workspace() -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    deploy = jobs["deploy"]
    assert isinstance(deploy, dict)
    steps = deploy["steps"]
    assert isinstance(steps, list)
    checkouts = [step for step in steps if step.get("uses") == "actions/checkout@v4"]
    assert checkouts == [
        {"uses": "actions/checkout@v4", "with": {"ref": "${{ env.TARGET_SHA }}"}},
        {
            "uses": "actions/checkout@v4",
            "with": {"ref": "${{ github.sha }}", "path": "current-main"},
        },
    ]
    runtime_apply = _step("deploy", "Terraform apply", "deploy-production.yml")
    origin_gate = _step("deploy", "Gate gateway origin", "deploy-production.yml")
    production_init = _step("deploy", "Terraform production edge init", "deploy-production.yml")
    production_apply = _step("deploy", "Terraform production edge apply", "deploy-production.yml")
    proxy_gate = _step("deploy", "Gate sandbox egress proxy TLS", "deploy-production.yml")
    shared_init = _step("deploy", "Terraform shared edge init", "deploy-production.yml")
    shared_apply = _step("deploy", "Terraform shared edge apply", "deploy-production.yml")
    door = _step("deploy", "Gate production door", "deploy-production.yml")
    assert all(
        steps.index(before) < steps.index(after)
        for before, after in pairwise(
            (
                checkouts[0],
                runtime_apply,
                origin_gate,
                production_init,
                production_apply,
                proxy_gate,
                checkouts[1],
                shared_init,
                shared_apply,
                door,
            )
        )
    )


def _check_production_shared_edge_uses_current_main() -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    deploy = jobs["deploy"]
    assert isinstance(deploy, dict)
    steps = deploy["steps"]
    assert isinstance(steps, list)
    checkout = next(
        step
        for step in steps
        if step.get("uses") == "actions/checkout@v4"
        and step.get("with", {}).get("path") == "current-main"
    )
    init = _step("deploy", "Terraform shared edge init", "deploy-production.yml")
    plan = _step("deploy", "Terraform shared edge plan", "deploy-production.yml")
    guard = _step("deploy", "Reject destructive shared edge changes", "deploy-production.yml")
    apply = _step("deploy", "Terraform shared edge apply", "deploy-production.yml")
    door = _step("deploy", "Gate production door", "deploy-production.yml")
    assert checkout["with"] == {"ref": "${{ github.sha }}", "path": "current-main"}
    assert all(
        step["working-directory"] == "current-main/infra/envs/edge"
        for step in (init, plan, guard, apply)
    )
    assert plan["env"] == {"TF_VAR_cloudflare_api_token": "${{ secrets.CLOUDFLARE_API_TOKEN }}"}
    assert tuple(re.findall(r"-target=(\S+)", plan["run"])) == (
        "cloudflare_ruleset.flyingobject_redirect",
        "cloudflare_ruleset.shipped_app_cache",
        "cloudflare_zone_setting.always_use_https",
    )
    assert '-out="$RUNNER_TEMP/shared-edge.tfplan"' in plan["run"]
    assert guard["run"] == (
        'terraform show -json "$RUNNER_TEMP/shared-edge.tfplan" | '
        'python "$GITHUB_WORKSPACE/current-main/.github/scripts/terraform_plan_guard.py"'
    )
    assert apply["run"] == 'terraform apply -input=false "$RUNNER_TEMP/shared-edge.tfplan"'
    assert all(
        steps.index(before) < steps.index(after)
        for before, after in pairwise((checkout, init, plan, guard, apply, door))
    )


def _check_edge_writers_share_deploy_concurrency() -> None:
    for workflow, job_name, group in [
        (
            "deploy.yml",
            "edge",
            "${{ github.event_name == 'pull_request' && "
            "format('deploy-edge-pr-{0}', github.event.pull_request.number) || 'deploy-edge' }}",
        ),
        ("deploy-production.yml", "deploy", "deploy-edge"),
    ]:
        job = _workflow(WORKFLOWS / workflow)["jobs"][job_name]
        assert isinstance(job, dict)
        assert job["concurrency"] == {"group": group, "cancel-in-progress": "false"}


def _check_pull_requests_guard_the_production_edge_plan() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    edge = jobs["edge"]
    assert isinstance(edge, dict)
    steps = edge["steps"]
    assert isinstance(steps, list)
    plan = _step("edge", "Terraform production edge plan")
    guard = _step("edge", "Reject destructive production edge changes")
    assert plan == {
        "name": "Terraform production edge plan",
        "if": "github.event_name == 'pull_request'",
        "working-directory": "infra/envs/edge",
        "env": {"TF_VAR_cloudflare_api_token": "${{ secrets.CLOUDFLARE_API_TOKEN }}"},
        "run": (
            "terraform plan -input=false -no-color -lock=false \\\n"
            "  -target=cloudflare_ruleset.flyingobject_redirect \\\n"
            "  -target=cloudflare_ruleset.shipped_app_cache \\\n"
            "  -target=cloudflare_zone_setting.always_use_https \\\n"
            "  -target=module.prod \\\n"
            '  -out="$RUNNER_TEMP/production-edge-review.tfplan"\n'
        ),
    }
    assert guard == {
        "name": "Reject destructive production edge changes",
        "if": "github.event_name == 'pull_request'",
        "working-directory": "infra/envs/edge",
        "run": (
            'terraform show -json "$RUNNER_TEMP/production-edge-review.tfplan" | '
            'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
        ),
    }
    assert steps.index(plan) < steps.index(guard)
    assert not any(
        "terraform apply" in str(step.get("run", ""))
        and "production-edge-review.tfplan" in str(step.get("run", ""))
        for step in steps
        if isinstance(step, dict)
    )


def _check_pull_requests_plan_production_foundation_without_applying() -> None:
    jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(jobs, dict)
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    assert "outputs" not in rollout
    rollout_steps = rollout["steps"]
    assert isinstance(rollout_steps, list)
    changes = jobs["changes"]
    assert isinstance(changes, dict)
    changes_steps = changes["steps"]
    assert isinstance(changes_steps, list)
    image_tag = next(step for step in changes_steps if step.get("name") == "Image tag")
    assert image_tag["id"] == "tag"
    assert 'echo "tag=$TAG" >> "$GITHUB_OUTPUT"' in image_tag["run"]
    assert "GITHUB_ENV" not in image_tag["run"]
    sandbox_template = next(
        step for step in rollout_steps if step.get("name") == "Select sandbox template"
    )
    assert "id" not in sandbox_template
    assert "E2B_TEMPLATES=$(uv run python sandbox/build_template.py)" in sandbox_template["run"]
    assert "GITHUB_OUTPUT" not in sandbox_template["run"]
    production = jobs["production"]
    assert isinstance(production, dict)
    assert production["env"] == {
        "E2B_TEMPLATES": "small=ufo-sbx-small,medium=ufo-sbx-medium,large=ufo-sbx-large",
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
        "  -target=module.platform.aws_secretsmanager_secret.postgres \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.platform \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.api_keys \\\n"
        "  -target=module.platform.aws_secretsmanager_secret.gateway_slack_connect \\\n"
        '  -var "e2b_templates=$E2B_TEMPLATES" \\\n'
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
    job = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]["prepare"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    preflight = _step("prepare", "Check production prerequisites", "deploy-production.yml")
    init = _step("prepare", "Terraform init", "deploy-production.yml")
    assert steps.index(preflight) < steps.index(init)
    assert preflight["run"] == _step("production_access", "Check production prerequisites")["run"]
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
        calls = invoked.splitlines()
        for service, code in (
            ("vpc", "L-F678F1CE"),
            ("ec2", "L-0263D0A3"),
            ("eks", "L-1194D53C"),
            ("rds", "L-7B6409FD"),
            ("elasticache", "L-DFE45DF3"),
            ("ec2", "L-1216C47A"),
            ("elasticloadbalancing", "L-69A177A2"),
            ("vpc", "L-FE5A380F"),
        ):
            assert (
                f"service-quotas get-service-quota --service-code {service} "
                f"--quota-code {code} --query Quota.Value --output text"
            ) in calls
        assert (
            "ec2 describe-subnets --subnet-ids subnet-a "
            "--query Subnets[0].AvailabilityZone --output text"
        ) in calls
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


def test_production_prerequisite_queries_fail_loud(tmp_path: Path) -> None:
    failed_commands = (
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
    )
    for index, failed_command in enumerate(failed_commands):
        case_path = tmp_path / str(index)
        case_path.mkdir()
        instances_used = (
            '[["m6i.large", null]]' if failed_command == "ec2 describe-instance-types" else "[]"
        )
        run, _ = _run_production_prerequisites(
            case_path,
            failed_command=failed_command,
            instances_used=instances_used,
        )
        assert run.returncode == 42, failed_command


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
    assert "ec2 L-1216C47A 54 8" in run.stdout.decode().splitlines()
    assert invoked.count("describe-instance-types") == 4
    assert (
        "ec2 describe-instance-types --instance-types m6i.large "
        "--query InstanceTypes[0].VCpuInfo.DefaultVCpus --output text"
    ) in invoked
    script = PRODUCTION_PREREQUISITES.read_text()
    assert 'select(test("^(?:[acdhmrtz][0-9]|i(?:[0-9]|m[0-9]|s[0-9]))"))' in script


def _check_production_vcpu_reservation_matches_the_node_group() -> None:
    production = (ROOT / "infra" / "envs" / "prod" / "main.tf").read_text()
    eks = (ROOT / "infra" / "modules" / "platform" / "eks.tf").read_text()
    instance_types = re.search(
        r'^  node_instance_types += +\["([^"]+)"\]$', production, re.MULTILINE
    )
    max_size = re.search(r"^  node_max_size += +(\d+)$", production, re.MULTILINE)
    az_count = re.search(r"^  az_count += +(\d+)$", production, re.MULTILINE)
    assert instance_types and instance_types.group(1) == "m6i.xlarge"
    assert max_size and az_count
    assert "use_latest_ami_release_version = true" in eks

    script = PRODUCTION_PREREQUISITES.read_text()
    reservation = re.search(
        r'REQUIRED=\$\(missing (\d+) "\$OWNED"\)\ncheck_headroom ec2 L-1216C47A',
        script,
    )
    assert reservation
    max_nodes = int(max_size.group(1))
    nodes = max_nodes + 2 * int(az_count.group(1))
    assert int(reservation.group(1)) == nodes * M6I_XLARGE_DEFAULT_VCPUS


def _check_production_nlb_reservation_matches_the_services() -> None:
    production = (ROOT / "infra" / "envs" / "prod" / "ufo.tf").read_text()
    load_balancers = production.count(
        '"service.beta.kubernetes.io/aws-load-balancer-nlb-target-type"'
    )
    script = PRODUCTION_PREREQUISITES.read_text()
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
        "ec2 L-1216C47A 56 0",
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
        "ec2 L-1216C47A 56 0",
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
        "ec2 L-1216C47A 48 8",
        "elasticloadbalancing L-69A177A2 0 4",
        "vpc L-FE5A380F 0 4",
        "vpc L-FE5A380F 0 4",
        "vpc L-FE5A380F 0 4",
    ]


def _check_hosted_runtime_receives_the_selected_sandbox_template() -> None:
    template = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    documents = template.split("\n---\n")
    for name in ("ufo-ingress", "ufo-serve"):
        deployment = next(
            document
            for document in documents
            if re.search(rf"^kind: Deployment\nmetadata:\n  name: {name}$", document, re.MULTILINE)
        )
        assert '- {name: E2B_TEMPLATES, value: "${e2b_templates}"}' in deployment
    for environment in DEPLOY_ENVIRONMENTS:
        root = ROOT / "infra" / "envs" / environment
        ufo_tf = (root / "ufo.tf").read_text()
        assert "e2b_templates                    = var.e2b_templates" in ufo_tf
        variables = (root / "variables.tf").read_text()
        assert 'variable "e2b_templates"' in variables
        assert 'condition     = var.e2b_templates != ""' in variables


def _check_hosted_serve_attests_the_deployed_bundle() -> None:
    template = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    deployment = next(
        document
        for document in template.split("\n---\n")
        if re.search(r"^kind: Deployment\nmetadata:\n  name: ufo-serve$", document, re.MULTILINE)
    )

    assert '- {name: UFO_RUNTIME_REVISION, value: "${image_tag}"}' in deployment
    assert '- {name: UFO_RUNTIME_IMAGE, value: "${bundle_image}"}' in deployment


def _check_saved_plans_reject_destructive_changes() -> None:
    for workflow, job_name, plan_name, step_name, working_directory in [
        ("deploy.yml", "rollout", "testing", "Terraform plan", "${{ env.TF_DIR }}"),
        ("deploy.yml", "edge", "edge", "Terraform plan", "infra/envs/edge"),
        (
            "deploy.yml",
            "production",
            "production",
            "Terraform foundation plan",
            "${{ env.TF_DIR }}",
        ),
        (
            "deploy.yml",
            "production_access",
            "production-access",
            "Terraform plan",
            "infra/production-access",
        ),
        (
            "deploy-production.yml",
            "prepare",
            "production-access",
            "Terraform plan",
            "infra/production-access",
        ),
        ("deploy-production.yml", "deploy", "production", "Terraform plan", "${{ env.TF_DIR }}"),
    ]:
        jobs = _workflow(WORKFLOWS / workflow)["jobs"]
        assert isinstance(jobs, dict)
        job = jobs[job_name]
        assert isinstance(job, dict)
        steps = job["steps"]
        assert isinstance(steps, list)
        setup = next(step for step in steps if step.get("uses") == "hashicorp/setup-terraform@v3")
        assert setup["with"]["terraform_wrapper"] == "false"
        plan = _step(job_name, step_name, workflow)
        guard = _step(job_name, "Reject destructive changes", workflow)
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


def _check_mutating_plans_lock_state_and_preserve_inputs() -> None:
    for job_name in ["rollout", "edge"]:
        script = _step(job_name, "Terraform plan")["run"]
        assert isinstance(script, str)
        assert "LOCK=true" in script
        assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then\n  LOCK=false\nfi' in script
        assert '-input=false -no-color -lock="$LOCK"' in script


def _check_rollout_plan_pins_the_selected_artifacts() -> None:
    script = _step("rollout", "Terraform plan")["run"]
    assert '-var "image_tag=$IMAGE_TAG"' in script
    assert '-var "e2b_templates=$E2B_TEMPLATES"' in script


def _check_apply_uses_the_guarded_plan() -> None:
    for workflow, job_name, plan_name, step_name, apply_condition in [
        (
            "deploy.yml",
            "rollout",
            "testing",
            "Terraform plan",
            "github.event_name != 'pull_request'",
        ),
        ("deploy.yml", "edge", "edge", "Terraform plan", "github.event_name != 'pull_request'"),
        ("deploy-production.yml", "prepare", "production-access", "Terraform plan", None),
    ]:
        plan = _step(job_name, step_name, workflow)
        guard = _step(job_name, "Reject destructive changes", workflow)
        apply = _step(job_name, "Terraform apply", workflow)
        assert plan.get("if") is None
        assert apply.get("if") == apply_condition
        assert apply["run"] == f'terraform apply -input=false "$RUNNER_TEMP/{plan_name}.tfplan"'
        steps = _workflow(WORKFLOWS / workflow)["jobs"][job_name]["steps"]
        assert steps.index(guard) < steps.index(apply)


def _check_only_testing_owns_account_global_resources() -> None:
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
        if "module.platform.ses_dkim_records" in source
    } == {Path("infra/envs/testing")}
    assert (
        "module.platform.ses_dkim_records"
        in environment_sources[ROOT / "infra" / "envs" / "testing"]
    )

    prod = environment_sources[ROOT / "infra" / "envs" / "prod"]
    assert not re.search(r'^resource\s+"cloudflare_', prod, re.MULTILINE)
    assert not re.search(r'^data\s+"cloudflare_', prod, re.MULTILINE)
    assert (
        "registry.terraform.io/cloudflare/cloudflare"
        in (ROOT / "infra" / "envs" / "prod" / ".terraform.lock.hcl").read_text()
    )
    platform_dns = re.findall(
        r'^resource\s+"cloudflare_dns_record"\s+"([^"]+)"',
        _code((ROOT / "infra" / "modules" / "platform" / "secrets.tf").read_text()),
        re.MULTILINE,
    )
    assert platform_dns == ["sandbox_proxy_validation"]


def _check_production_deploy_role_trusts_only_the_main_production_workflow() -> None:
    source = _code((ROOT / "infra" / "production-access" / "main.tf").read_text())
    trust = _terraform_block(source, "data", "github_trust")
    workflow_name = _workflow(WORKFLOWS / "deploy-production.yml")["name"]
    assert isinstance(workflow_name, str)
    assert [
        path
        for path in (*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))
        if _workflow(path).get("name") == workflow_name
    ] == [WORKFLOWS / "deploy-production.yml"]
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


def _check_production_deploy_consumes_the_protected_role_and_artifacts() -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    prepare = jobs["prepare"]
    assert isinstance(prepare, dict)
    job = jobs["deploy"]
    assert isinstance(job, dict)
    assert job["needs"] == "prepare"
    assert job["environment"] == "production"
    assert job["env"] == {
        "DD_API_KEY": "${{ secrets.DD_API_KEY }}",
        "DD_APP_KEY": "${{ secrets.DD_APP_KEY }}",
        "DEPLOY_ROLE_ARN": "arn:aws:iam::899147036157:role/github-production-deploy",
        "IMAGE_TAG": "${{ needs.prepare.outputs.image_tag }}",
        "TARGET_SHA": "${{ needs.prepare.outputs.target_sha }}",
    }
    steps = job["steps"]
    assert isinstance(steps, list)
    credentials = next(
        step for step in steps if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
    )
    assert credentials["with"]["role-to-assume"] == "${{ env.DEPLOY_ROLE_ARN }}"
    source = (WORKFLOWS / "deploy-production.yml").read_text()
    assert "docker build" not in source
    assert "docker push" not in source


def test_production_publishes_its_own_sandbox_templates(tmp_path: Path) -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["deploy"]
    assert isinstance(job, dict)
    assert "E2B_TEMPLATES" not in job["env"]
    assert "e2b_templates" not in jobs["prepare"]["outputs"]
    assert (
        "needs.prepare.outputs.e2b_templates"
        not in (WORKFLOWS / "deploy-production.yml").read_text()
    )
    steps = job["steps"]
    assert isinstance(steps, list)
    select = _step("deploy", "Select sandbox template", "deploy-production.yml")
    assert select["env"] == {"E2B_API_KEY": "${{ secrets.E2B_API_KEY }}"}
    promoted = steps[1]
    uv = next(step for step in steps if step.get("uses") == "astral-sh/setup-uv@v5")
    credentials = next(
        step for step in steps if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
    )
    foundation = _step("deploy", "Terraform foundation plan", "deploy-production.yml")
    assert (
        steps.index(promoted)
        < steps.index(uv)
        < steps.index(select)
        < steps.index(credentials)
        < steps.index(foundation)
    )

    script = select["run"]
    assert isinstance(script, str)
    shim = tmp_path / "bin"
    shim.mkdir()
    call = tmp_path / "uv-call"
    uv_shim = shim / "uv"
    uv_shim.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$UV_CALL"\nprintf "%s" "$BUILD_OUTPUT"\n')
    uv_shim.chmod(0o755)
    github_env = tmp_path / "github-env"

    def build(output: str) -> subprocess.CompletedProcess[bytes]:
        github_env.write_text("")
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            capture_output=True,
            env={
                "BUILD_OUTPUT": output,
                "GITHUB_ENV": str(github_env),
                "PATH": f"{shim}:{os.environ['PATH']}",
                "UV_CALL": str(call),
            },
        )

    published = build(TESTED_TEMPLATES)
    assert published.returncode == 0, published.stderr.decode()
    assert call.read_text().strip() == "run python sandbox/build_template.py"
    assert github_env.read_text().splitlines() == [f"E2B_TEMPLATES={TESTED_TEMPLATES}"]
    assert build("").returncode != 0
    assert github_env.read_text() == ""


def test_testing_run_records_the_artifacts_production_consumes(tmp_path: Path) -> None:
    testing_jobs = _workflow(WORKFLOWS / "deploy.yml")["jobs"]
    assert isinstance(testing_jobs, dict)
    rollout = testing_jobs["rollout"]
    assert isinstance(rollout, dict)
    rollout_steps = rollout["steps"]
    assert isinstance(rollout_steps, list)
    record = _step("rollout", "Record tested artifacts")
    upload = next(
        step for step in rollout_steps if step.get("uses") == "actions/upload-artifact@v4"
    )
    assert record["if"] == "github.event_name == 'push'"
    assert upload["if"] == "github.event_name == 'push'"
    assert upload["with"] == {
        "name": "tested-artifacts",
        "path": "${{ runner.temp }}/tested-artifacts.json",
        "if-no-files-found": "error",
        "overwrite": "true",
        "retention-days": "90",
    }
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", record["run"]],
        check=True,
        env={
            "E2B_TEMPLATES": TESTED_TEMPLATES,
            "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
            "IMAGE_TAG": "01234567",
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": str(tmp_path),
        },
    )
    artifact = tmp_path / "tested-artifacts.json"
    assert [path.name for path in tmp_path.iterdir()] == [artifact.name]
    assert json.loads(artifact.read_text()) == {
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "image_tag": "01234567",
        "sandbox_templates": TESTED_TEMPLATES,
    }
    assert rollout_steps.index(record) < rollout_steps.index(upload)

    production_jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    production_workflow = _workflow(WORKFLOWS / "deploy-production.yml")
    assert production_workflow["permissions"]["actions"] == "read"
    assert isinstance(production_jobs, dict)
    prepare = production_jobs["prepare"]
    assert isinstance(prepare, dict)
    assert prepare["outputs"] == {
        "attempt_id": "${{ steps.attempt.outputs.attempt_id }}",
        "image_tag": "${{ steps.artifacts.outputs.image_tag }}",
        "target_sha": "${{ steps.select.outputs.target_sha }}",
    }
    prepare_steps = prepare["steps"]
    assert isinstance(prepare_steps, list)
    select = _step("prepare", "Select tested deployment", "deploy-production.yml")
    download = next(
        step for step in prepare_steps if step.get("uses") == "actions/download-artifact@v4"
    )
    verify = _step("prepare", "Verify tested artifacts", "deploy-production.yml")
    assert download["with"] == {
        "name": upload["with"]["name"],
        "path": "${{ runner.temp }}/tested-artifacts",
        "run-id": "${{ steps.select.outputs.testing_run_id }}",
        "github-token": "${{ github.token }}",
    }
    assert "${{ inputs.target_sha }}" in str(select)
    assert "actions/workflows/deploy.yml/runs" in select["run"]
    assert "push" in select["run"]
    assert 'test "$TESTED_STATUS" = completed' in select["run"]
    assert 'test "$TESTED_CONCLUSION" = success' in select["run"]
    assert 'ARTIFACT="$RUNNER_TEMP/tested-artifacts/tested-artifacts.json"' in verify["run"]
    assert "TARGET_SHA" in verify["run"]
    assert "image_tag" in verify["run"]
    assert "sandbox_templates" in verify["run"]
    assert prepare_steps.index(select) < prepare_steps.index(download) < prepare_steps.index(verify)


def test_production_target_selection_fails_closed(tmp_path: Path) -> None:
    script = _step("prepare", "Select tested deployment", "deploy-production.yml")["run"]
    assert isinstance(script, str)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "commit", "--allow-empty", "-m", "tested"], cwd=repo, check=True)
    tested_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run([*git, "commit", "--allow-empty", "-m", "main"], cwd=repo, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True)
    subprocess.run(["git", "switch", "--orphan", "diverged"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "--allow-empty", "-m", "diverged"], cwd=repo, check=True)
    diverged_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "switch", "main"], cwd=repo, check=True)
    response = tmp_path / "response.json"
    output = tmp_path / "github-output"
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --jq ]; then jq -r "$2" "$GH_RESPONSE"; exit; fi\n'
        "  shift\n"
        "done\n"
        "exit 1\n"
    )
    gh.chmod(0o755)

    def select(
        target_sha: str, workflow_runs: list[dict[str, object]]
    ) -> subprocess.CompletedProcess[bytes]:
        response.write_text(json.dumps({"workflow_runs": workflow_runs}))
        output.write_text("")
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=repo,
            capture_output=True,
            env={
                "GH_RESPONSE": str(response),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "metalcraftai/ufo",
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "TARGET_SHA": target_sha,
            },
        )

    successful_run = [{"id": 123, "status": "completed", "conclusion": "success"}]
    accepted = select(tested_sha, successful_run)
    assert accepted.returncode == 0, accepted.stderr.decode()
    assert output.read_text().splitlines() == [
        f"target_sha={tested_sha}",
        "testing_run_id=123",
    ]
    assert select(diverged_sha, successful_run).returncode != 0
    assert select(tested_sha, []).returncode != 0


def test_production_prepare_rejects_untested_artifact_values(tmp_path: Path) -> None:
    script = _step("prepare", "Verify tested artifacts", "deploy-production.yml")["run"]
    assert isinstance(script, str)
    target_sha = "0123456789abcdef0123456789abcdef01234567"
    artifact_dir = tmp_path / "tested-artifacts"
    artifact_dir.mkdir()
    artifact = artifact_dir / "tested-artifacts.json"
    output = tmp_path / "github-output"

    def verify(values: dict[str, object]) -> subprocess.CompletedProcess[bytes]:
        artifact.write_text(json.dumps(values))
        output.write_text("")
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            capture_output=True,
            env={
                "GITHUB_OUTPUT": str(output),
                "RUNNER_TEMP": str(tmp_path),
                "TARGET_SHA": target_sha,
                "PATH": os.environ["PATH"],
            },
        )

    values = {
        "commit": target_sha,
        "image_tag": target_sha[:8],
        "sandbox_templates": TESTED_TEMPLATES,
    }
    accepted = verify(values)
    assert accepted.returncode == 0, accepted.stderr.decode()
    assert output.read_text().splitlines() == [f"image_tag={target_sha[:8]}"]
    assert verify(values | {"commit": "f" * 40}).returncode != 0
    assert verify(values | {"image_tag": "ffffffff"}).returncode != 0
    assert verify(values | {"sandbox_templates": ""}).returncode != 0
    assert verify(values | {"sandbox_templates": None}).returncode != 0
    assert verify({"commit": target_sha, "image_tag": target_sha[:8]}).returncode != 0


def _check_production_secrets_fail_before_aws_changes() -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    validate = jobs["validate"]
    prepare = jobs["prepare"]
    assert isinstance(validate, dict)
    assert isinstance(prepare, dict)
    ref = jobs["ref"]
    assert isinstance(ref, dict)
    assert "if" not in ref
    assert ref["steps"] == [
        {
            "name": "Require main branch",
            "env": {"WORKFLOW_REF": "${{ github.ref }}"},
            "run": 'test "$WORKFLOW_REF" = refs/heads/main',
        }
    ]
    ref_gate = _step("ref", "Require main branch", "deploy-production.yml")
    assert (
        subprocess.run(
            ["bash", "-e", "-c", ref_gate["run"]],
            env={"WORKFLOW_REF": "refs/heads/main"},
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["bash", "-e", "-c", ref_gate["run"]],
            env={"WORKFLOW_REF": "refs/heads/feature"},
        ).returncode
        != 0
    )
    assert validate["needs"] == "ref"
    assert validate["environment"] == "production"
    assert prepare["needs"] == "validate"
    assert len(validate["steps"]) == 1
    step = _step("validate", "Require production secrets", "deploy-production.yml")
    required = (
        "ANTHROPIC_API_KEY",
        "BROWSERBASE_API_KEY",
        "CLOUDFLARE_API_TOKEN",
        "DD_API_KEY",
        "DD_APP_KEY",
        "E2B_API_KEY",
        "PERPLEXITY_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "SPECTRUM_PROJECT_ID",
        "SPECTRUM_PROJECT_SECRET",
        "TURBOPUFFER_API_KEY",
    )
    assert step["env"] == {name: f"${{{{ secrets.{name} }}}}" for name in required}
    environment = dict.fromkeys(required, "present")
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], check=True, env=environment)
    missing = required[::2]
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        capture_output=True,
        env=environment | dict.fromkeys(missing, ""),
    )
    assert failed.returncode != 0
    assert failed.stdout.decode().splitlines() == [
        f"::error::{name} is required" for name in missing
    ]
    assert b"present" not in failed.stdout + failed.stderr


def _check_production_prepare_applies_access_before_deploy() -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    prepare = jobs["prepare"]
    assert isinstance(prepare, dict)
    assert "environment" not in prepare
    assert prepare["needs"] == "validate"
    steps = prepare["steps"]
    assert isinstance(steps, list)
    credentials = next(
        step for step in steps if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
    )
    assert credentials["with"]["role-to-assume"] == ("arn:aws:iam::899147036157:role/github-deploy")
    init = _step("prepare", "Terraform init", "deploy-production.yml")
    plan = _step("prepare", "Terraform plan", "deploy-production.yml")
    guard = _step("prepare", "Reject destructive changes", "deploy-production.yml")
    apply = _step("prepare", "Terraform apply", "deploy-production.yml")
    boundary = _step("prepare", "Reject an unsplit authorization change", "deploy-production.yml")
    attempt = _step("prepare", "Record the production attempt", "deploy-production.yml")
    prerequisites = _step("prepare", "Check production prerequisites", "deploy-production.yml")
    images = _step("prepare", "Require tested images", "deploy-production.yml")
    assert "task=deploy%3Aproduction&per_page=1" in boundary["run"]
    checkouts = [step for step in steps if step.get("uses") == "actions/checkout@v4"]
    assert checkouts[-2]["with"]["ref"] == "${{ steps.select.outputs.target_sha }}"
    assert checkouts[-1]["with"]["ref"] == "${{ github.sha }}"
    assert init["working-directory"] == "infra/production-access"
    assert plan["working-directory"] == "infra/production-access"
    assert guard["working-directory"] == "infra/production-access"
    assert apply["working-directory"] == "infra/production-access"
    assert apply["run"] == 'terraform apply -input=false "$RUNNER_TEMP/production-access.tfplan"'
    assert all(
        steps.index(checkouts[-2]) < steps.index(step) < steps.index(checkouts[-1])
        for step in (prerequisites, boundary)
    )
    assert steps.index(boundary) < steps.index(attempt) < steps.index(apply)
    assert steps.index(images) < steps.index(apply)
    assert all(
        steps.index(before) < steps.index(after)
        for before, after in pairwise((checkouts[-1], plan, guard, apply))
    )


def test_production_prepare_requires_both_tested_images(tmp_path: Path) -> None:
    step = _step("prepare", "Require tested images", "deploy-production.yml")
    assert step["env"] == {"IMAGE_TAG": "${{ steps.artifacts.outputs.image_tag }}"}
    script = step["run"]
    aws = tmp_path / "aws"
    aws.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$AWS_CALLS"\n[ "$FAIL_REPOSITORY" != "$4" ]\n'
    )
    aws.chmod(0o755)
    calls = tmp_path / "aws-calls"
    environment = {
        "AWS_CALLS": str(calls),
        "FAIL_REPOSITORY": "",
        "IMAGE_TAG": "01234567",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], check=True, env=environment)
    assert calls.read_text().splitlines() == [
        "ecr describe-images --repository-name ufo --image-ids imageTag=01234567",
        "ecr describe-images --repository-name ufo-control --image-ids imageTag=01234567",
    ]
    calls.write_text("")
    failed_first = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env=environment | {"FAIL_REPOSITORY": "ufo"},
    )
    assert failed_first.returncode != 0
    assert calls.read_text().splitlines() == [
        "ecr describe-images --repository-name ufo --image-ids imageTag=01234567"
    ]
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env=environment | {"FAIL_REPOSITORY": "ufo-control"},
    )
    assert failed.returncode != 0


def test_production_authorization_boundary_fails_closed(tmp_path: Path) -> None:
    step = _step("prepare", "Reject an unsplit authorization change", "deploy-production.yml")
    assert step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "REPLACE_ATTEMPT_SHA": "${{ inputs.replace_attempt_sha }}",
        "TARGET_SHA": "${{ steps.select.outputs.target_sha }}",
    }
    script = step["run"]
    repo = tmp_path / "repo"
    gate = repo / ".github" / "scripts" / "deploy_change_gate.py"
    gate.parent.mkdir(parents=True)
    shutil.copy(ROOT / ".github" / "scripts" / "deploy_change_gate.py", gate)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    # Seed an existing grant on base so the target commit can remove one (a contraction).
    authorization = repo / "infra" / "modules" / "platform" / "iam.tf"
    authorization.parent.mkdir(parents=True)
    authorization.write_text('policy "keep" {}\npolicy "drop" {}\n')
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "add",
            ".",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "base",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    # Contraction: drop the "drop" grant, landing beside a runtime (workflow) change.
    authorization.write_text('policy "keep" {}\n')
    workflow = repo / ".github" / "workflows" / "deploy-production.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("changed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "target",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    target_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "switch", "-c", "span", target_sha], cwd=repo, check=True)
    runtime = repo / "core" / "src" / "ufo" / "serve.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("changed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "span",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    span_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "switch", "-c", "clean", base_sha], cwd=repo, check=True)
    runtime.parent.mkdir(parents=True)
    runtime.write_text("changed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "clean",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    clean_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$GH_CALLS"\n'
        'case "$*" in\n'
        "  *task=deploy%3Aproduction-attempt*) printf '%s\\n' \"$GH_ATTEMPT\" ;;\n"
        "  *task=deploy%3Aproduction*) printf '%s\\n' \"$GH_SUCCESS\" ;;\n"
        "  *) exit 42 ;;\n"
        "esac\n"
    )
    gh.chmod(0o755)
    aws = tmp_path / "aws"
    aws.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$AWS_CALLS"\n'
        'case "$AWS_CLUSTER_STATE" in\n'
        "  found) exit 0 ;;\n"
        "  missing) printf '%s\\n' ResourceNotFoundException >&2; exit 254 ;;\n"
        "  *) printf '%s\\n' AccessDeniedException >&2; exit 254 ;;\n"
        "esac\n"
    )
    aws.chmod(0o755)
    aws_calls = tmp_path / "aws-calls"
    gh_calls = tmp_path / "gh-calls"

    def boundary(
        success: str,
        attempt: str,
        target: str = target_sha,
        cluster: str = "missing",
        replace: str = "",
    ) -> subprocess.CompletedProcess[bytes]:
        aws_calls.write_text("")
        gh_calls.write_text("")
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=repo,
            capture_output=True,
            env={
                "AWS_CALLS": str(aws_calls),
                "AWS_CLUSTER_STATE": cluster,
                "GH_ATTEMPT": attempt,
                "GH_CALLS": str(gh_calls),
                "GH_SUCCESS": success,
                "GITHUB_REPOSITORY": "metalcraftai/ufo",
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "REPLACE_ATTEMPT_SHA": replace,
                "RUNNER_TEMP": str(tmp_path),
                "TARGET_SHA": target,
            },
        )

    success_query = (
        "api repos/metalcraftai/ufo/deployments?environment=production&"
        "task=deploy%3Aproduction&per_page=1 --jq .[0].sha // empty"
    )
    attempt_query = (
        "api repos/metalcraftai/ufo/deployments?environment=production&"
        "task=deploy%3Aproduction-attempt&per_page=1 --jq .[0].sha // empty"
    )
    split = boundary(base_sha, target_sha, span_sha)
    assert split.returncode != 0
    assert b"contract IAM only after" in split.stderr
    assert gh_calls.read_text().splitlines() == [success_query]
    assert boundary(base_sha, target_sha, clean_sha).returncode == 0
    assert boundary(base_sha, target_sha, span_sha, replace=target_sha).returncode != 0
    assert boundary(target_sha, "", clean_sha).returncode != 0
    assert boundary("", target_sha, target_sha).returncode == 0
    assert gh_calls.read_text().splitlines() == [success_query, attempt_query]
    assert aws_calls.read_text() == ""
    changed = boundary("", target_sha, span_sha)
    assert changed.returncode != 0
    assert changed.stdout.decode() == (
        f"::error::Production bootstrap requires replace_attempt_sha={target_sha}.\n"
    )
    assert boundary("", target_sha, span_sha, replace=target_sha).returncode == 0
    assert boundary("", target_sha, span_sha, replace=base_sha).returncode != 0
    replacement_span = boundary("", base_sha, span_sha, replace=base_sha)
    assert replacement_span.returncode != 0
    assert b"contract IAM only after" in replacement_span.stderr
    existing = boundary("", "", cluster="found")
    assert existing.returncode != 0
    assert existing.stdout == b"::error::No recorded production deployment.\n"
    assert boundary("", "").returncode == 0
    assert gh_calls.read_text().splitlines() == [success_query, attempt_query]
    denied = boundary("", "", cluster="denied")
    assert denied.returncode != 0
    assert denied.stderr == b"AccessDeniedException\n"
    assert aws_calls.read_text().splitlines() == ["eks describe-cluster --name prod-cluster"]


def _check_production_deploy_rejects_missing_inputs_before_role_assumption() -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["deploy"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    step = _step("deploy", "Require production inputs", "deploy-production.yml")
    assert steps[0] == step
    assert steps[1] == {
        "uses": "actions/checkout@v4",
        "with": {"ref": "${{ env.TARGET_SHA }}"},
    }
    assert "env" not in step
    required = ("IMAGE_TAG", "TARGET_SHA")
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
    missing = required
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        capture_output=True,
        env=environment | dict.fromkeys(missing, ""),
    )
    assert failed.returncode != 0
    assert failed.stdout.decode().splitlines() == [
        f"::error::{name} is required" for name in missing
    ]


def test_production_gateway_origin_gate_executes(tmp_path: Path) -> None:
    gate = _step("deploy", "Gate gateway origin", "deploy-production.yml")
    assert gate["shell"] == "bash"
    script = gate["run"]
    terraform = tmp_path / "terraform"
    terraform.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$*" > "$TERRAFORM_CALL"\nprintf \'%s\\n\' "$ORIGIN_HOST"\n'
    )
    terraform.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$CURL_CALLS"\n'
        'case "$*" in\n'
        '  *"/v1/onboard/ufo"*)\n'
        "    [ \"$BAD_RESPONSE\" != onboard ] || { printf 'wrong\\n'; exit; }\n"
        "    printf 'x-ufo-session header is required.\\n'\n"
        "    ;;\n"
        '  *"/ufo/bin/"*)\n'
        "    [ \"$BAD_RESPONSE\" != binary ] || { printf '404'; exit; }\n"
        "    printf '200'\n"
        "    ;;\n"
        "  */ufo)\n"
        "    [ \"$BAD_RESPONSE\" != ufo ] || { printf 'wrong\\n'; exit; }\n"
        '    printf \'UFO_URL="${UFO_URL:-https://%s}"\\n\' "$ORIGIN_HOST"\n'
        "    ;;\n"
        "  */fleet)\n"
        '    [ "$BAD_RESPONSE" != fleet ] || { printf \'{"craft":"wrong"}\\n\'; exit; }\n'
        "    printf '{\"craft\":1}\\n'\n"
        "    ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    calls = tmp_path / "curl-calls"
    environment = {
        "BAD_RESPONSE": "",
        "CURL_CALLS": str(calls),
        "ORIGIN_HOST": "ufo.ai",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TERRAFORM_CALL": str(tmp_path / "terraform-call"),
        "TF_DIR": "infra/envs/prod",
    }
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], check=True, env=environment)
    assert (tmp_path / "terraform-call").read_text().strip() == (
        "-chdir=infra/envs/prod output -raw hostname"
    )
    assert calls.read_text().splitlines() == [
        "-fsS -X POST https://origin.ufo.ai/v1/onboard/ufo",
        "-fsS https://origin.ufo.ai/ufo",
        "-fsS -o /dev/null -w %{http_code} https://origin.ufo.ai/ufo/bin/x86_64-unknown-linux-musl",
        "-fsS https://origin.ufo.ai/fleet",
    ]
    for bad_response in ("onboard", "ufo", "binary", "fleet"):
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            capture_output=True,
            env=environment | {"BAD_RESPONSE": bad_response},
        )
        assert failed.returncode != 0


def _check_production_deploy_applies_guarded_foundation_then_runtime() -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["deploy"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)

    foundation_plan = _step("deploy", "Terraform foundation plan", "deploy-production.yml")
    foundation_guard = _step(
        "deploy", "Reject destructive foundation changes", "deploy-production.yml"
    )
    foundation_apply = _step("deploy", "Terraform foundation apply", "deploy-production.yml")
    secrets = _step("deploy", "Write production runtime secrets", "deploy-production.yml")
    runtime_plan = _step("deploy", "Terraform plan", "deploy-production.yml")
    runtime_guard = _step("deploy", "Reject destructive changes", "deploy-production.yml")
    refresh = _step("deploy", "Refresh production runtime secrets", "deploy-production.yml")
    runtime_apply = _step("deploy", "Terraform apply", "deploy-production.yml")
    rollout = _step("deploy", "Wait for runtime rollout", "deploy-production.yml")
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
        '  -var "image_tag=$IMAGE_TAG" -var "e2b_templates=$E2B_TEMPLATES" \\\n'
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
            "E2B_API_KEY": "${{ secrets.E2B_API_KEY }}",
            "PERPLEXITY_API_KEY": "${{ secrets.PERPLEXITY_API_KEY }}",
            "OPENAI_API_KEY": "${{ secrets.OPENAI_API_KEY }}",
            "OPENROUTER_API_KEY": "${{ secrets.OPENROUTER_API_KEY }}",
            "SPECTRUM_PROJECT_ID": "${{ secrets.SPECTRUM_PROJECT_ID }}",
            "SPECTRUM_PROJECT_SECRET": "${{ secrets.SPECTRUM_PROJECT_SECRET }}",
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
        '-var "image_tag=$IMAGE_TAG" -var "e2b_templates=$E2B_TEMPLATES" '
        '-var "deployment_id=$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"'
    )
    assert runtime_guard["run"] == (
        'terraform show -json "$RUNNER_TEMP/production.tfplan" | '
        'python "$GITHUB_WORKSPACE/.github/scripts/terraform_plan_guard.py"'
    )
    assert runtime_apply["run"] == 'terraform apply -input=false "$RUNNER_TEMP/production.tfplan"'
    for step in (
        foundation_plan,
        foundation_guard,
        foundation_apply,
        runtime_plan,
        runtime_guard,
        runtime_apply,
    ):
        assert step["working-directory"] == "${{ env.TF_DIR }}"
    assert refresh["run"] == (
        "aws eks update-kubeconfig \\\n"
        '  --region "$AWS_REGION" \\\n'
        '  --name "$(terraform -chdir="$TF_DIR" output -raw cluster_name)"\n'
        'NAMESPACE="$(terraform -chdir="$TF_DIR" show -json '
        '"$RUNNER_TEMP/production.tfplan" \\\n'
        "  | jq -er '.planned_values.outputs.system_namespace.value')\"\n"
        "python infra/secret_sync.py \\\n"
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


def test_production_refresh_reads_the_planned_namespace(tmp_path: Path) -> None:
    script = _step("deploy", "Refresh production runtime secrets", "deploy-production.yml")["run"]
    terraform = tmp_path / "terraform"
    terraform.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$TERRAFORM_CALLS"\n'
        'case "$*" in\n'
        "  *'output -raw cluster_name') printf 'prod-cluster\\n' ;;\n"
        "  *'show -json '*production.tfplan) "
        'printf \'{"planned_values":{"outputs":{"system_namespace":'
        '{"value":"ufo-system"}}}}\' ;;\n'
        "  *) exit 42 ;;\n"
        "esac\n"
    )
    terraform.chmod(0o755)
    aws = tmp_path / "aws"
    aws.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" > "$AWS_CALL"\n')
    aws.chmod(0o755)
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" > "$PYTHON_CALL"\n')
    python.chmod(0o755)
    terraform_calls = tmp_path / "terraform-calls"
    aws_call = tmp_path / "aws-call"
    python_call = tmp_path / "python-call"

    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        check=True,
        env={
            "AWS_CALL": str(aws_call),
            "AWS_REGION": "us-east-1",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_RUN_ID": "30774596746",
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "PYTHON_CALL": str(python_call),
            "RUNNER_TEMP": str(tmp_path),
            "TERRAFORM_CALLS": str(terraform_calls),
            "TF_DIR": "infra/envs/prod",
        },
    )

    assert terraform_calls.read_text().splitlines() == [
        "-chdir=infra/envs/prod output -raw cluster_name",
        f"-chdir=infra/envs/prod show -json {tmp_path}/production.tfplan",
    ]
    assert aws_call.read_text().strip() == (
        "eks update-kubeconfig --region us-east-1 --name prod-cluster"
    )
    assert python_call.read_text().strip() == ("infra/secret_sync.py ufo-system 30774596746-2")


def _check_proxy_gate_dials_the_rolled_proxy_with_the_shared_ca() -> None:
    for workflow, job_name in [("deploy.yml", "rollout"), ("deploy-production.yml", "deploy")]:
        jobs = _workflow(WORKFLOWS / workflow)["jobs"]
        assert isinstance(jobs, dict)
        job = jobs[job_name]
        assert isinstance(job, dict)
        steps = job["steps"]
        assert isinstance(steps, list)
        gate = next(step for step in steps if step.get("name") == "Gate sandbox egress proxy TLS")
        selector = next(step for step in steps if step.get("name") == "Select sandbox template")
        assert 'echo "E2B_TEMPLATES=$E2B_TEMPLATES" >> "$GITHUB_ENV"' in selector["run"]
        assert steps.index(selector) < steps.index(gate)
        script = gate["run"]
        assert isinstance(script, str)
        assert "output -raw sandbox_proxy_ca_cert" in script
        assert "output -raw sandbox_proxy_url" in script
        assert "sandbox/proxy_gate.py" in script


def _check_the_template_publish_takes_the_client_binary_a_pipeline_already_built() -> None:
    """The sandbox image bakes the compiled `ufo` client, so publishing a template needs a linux
    binary before `build_template.py` runs. The testing deploy takes the one its own client job
    staged — or, when that job was skipped because this client tree already built, the pushed set it
    reuses — so no second Rust build appears here. The production deploy runs no client job at all,
    so it builds the crate for the sandbox target itself, before the publish."""
    testing = _workflow(WORKFLOWS / "deploy.yml")
    environment = testing["env"]
    assert isinstance(environment, dict)
    assert environment["SANDBOX_CLIENT_BINARY"] == (
        "servers/control/clientbin/x86_64-unknown-linux-musl/ufo"
    )
    jobs = testing["jobs"]
    assert isinstance(jobs, dict)
    rollout = jobs["rollout"]
    assert isinstance(rollout, dict)
    steps = rollout["steps"]
    assert isinstance(steps, list)
    named = "${{ env.SANDBOX_CLIENT_BINARY }}"
    publish = _step("rollout", "Select sandbox template")
    assert publish["env"]["UFO_CLIENT_BINARY"] == named
    assert steps.index(publish) > steps.index(
        next(step for step in steps if step.get("uses") == "actions/download-artifact@v4")
    )
    assert steps.index(publish) > steps.index(_step("rollout", "Reuse the pushed client binaries"))
    assert not any("cargo build" in step.get("run", "") for step in steps)

    production = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(production, dict)
    deploy = production["deploy"]
    assert isinstance(deploy, dict)
    prod_steps = deploy["steps"]
    assert isinstance(prod_steps, list)
    built = next(step for step in prod_steps if "cargo build --release" in step.get("run", ""))
    assert built["working-directory"] == "client"
    assert "x86_64-unknown-linux-musl" in built["run"]
    assert prod_steps.index(built) < prod_steps.index(
        _step("deploy", "Select sandbox template", "deploy-production.yml")
    )


def _check_hosted_namespaces_hold_rollouts_until_nlb_targets_are_ready() -> None:
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


def _check_sandbox_proxy_nlb_routes_across_all_enabled_zones() -> None:
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


def _check_runtime_rollout_drains_before_the_proxy_gate() -> None:
    for workflow, job_name in [("deploy.yml", "rollout"), ("deploy-production.yml", "deploy")]:
        job = _workflow(WORKFLOWS / workflow)["jobs"][job_name]
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
        templates = ROOT / "infra" / "templates"
        manifests = [
            document
            for source in (
                re.sub(
                    TEMPLATE_DIRECTIVE,
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
            "externalsecret/ufo-gateway-workos",
            "externalsecret/ufo-egress-ca",
            "externalsecret/datadog-api-key",
        }
        assert rollout_resources == {
            "daemonset/otel-logs-agent",
            "deployment/otel-collector",
            "deployment/ufo-gateway",
            "deployment/ufo-preview",
            "deployment/ufo-sandbox-proxy",
            "deployment/ufo-ingress",
            "deployment/ufo-serve",
            "deployment/ufo-jobs",
        }
        commands = [
            shlex.split(line)
            for line in script.replace("\\\n", " ").splitlines()
            if line.strip().startswith(("kubectl ", f"{AWAIT_ROLLOUT} "))
        ]
        awaited = [command for command in commands if command[0] == AWAIT_ROLLOUT]
        kubectl_commands = [command for command in commands if command[0] == "kubectl"]
        assert all(command[1] == "--namespace" for command in kubectl_commands)
        wait_commands = [command for command in kubectl_commands if command[3] == "wait"]
        rollout_commands = [command for command in kubectl_commands if command[3] == "rollout"]
        assert len(kubectl_commands) == len(wait_commands) + len(rollout_commands)
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
        assert all(
            len(command) >= 3 and all(not argument.startswith("-") for argument in command[1:])
            for command in awaited
        )
        gated_readiness = {resource for command in wait_commands for resource in command[6:]}
        gated_rollouts: dict[str, set[str]] = {}
        for command in awaited:
            gated_rollouts.setdefault(command[1], set()).update(
                f"deployment/{name}" for name in command[2:]
            )
        for command in rollout_commands:
            gated_rollouts.setdefault(command[2], set()).add(command[5])
        assert gated_readiness == readiness_resources
        assert gated_rollouts == {
            "ingress-nginx": {"deployment/ingress-nginx-controller"},
            "$NAMESPACE": rollout_resources,
        }
        assert os.access(ROOT / AWAIT_ROLLOUT, os.X_OK)
        # The 15m wall the per-Deployment `rollout status` calls carried now lives in the script.
        assert "DEADLINE=$((SECONDS + 900))" in (ROOT / AWAIT_ROLLOUT).read_text()


def _check_runtime_apply_waits_for_prior_drains() -> None:
    for workflow, job_name, refresh in [
        ("deploy.yml", "rollout", "Refresh testing runtime secrets"),
        ("deploy-production.yml", "deploy", "Refresh production runtime secrets"),
    ]:
        job = _workflow(WORKFLOWS / workflow)["jobs"][job_name]
        assert isinstance(job, dict)
        steps = job["steps"]
        assert isinstance(steps, list)
        names = [step.get("name") for step in steps if isinstance(step, dict)]
        wait = names.index("Wait for prior rollout drains")
        assert names.index(refresh) < wait < names.index("Terraform apply")
        step = steps[wait]
        assert isinstance(step, dict)
        assert step.get("if") == (
            "github.event_name != 'pull_request'" if workflow == "deploy.yml" else None
        )
        script = step["run"]
        assert isinstance(script, str)
        assert "output -raw system_namespace" in script
        assert shlex.split(script.splitlines()[-1]) == [
            AWAIT_DRAINED,
            "$NAMESPACE",
            "ufo-sandbox-proxy",
            "ufo-ingress",
            "ufo-serve",
            "ufo-jobs",
        ]
        assert os.access(ROOT / AWAIT_DRAINED, os.X_OK)


def test_await_drained_waits_for_terminating_pods(tmp_path: Path) -> None:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  'get namespace/system --ignore-not-found -o name') printf 'namespace/system\\n' ;;\n"
        "  *'get pods -l app=ufo-sandbox-proxy -o json')\n"
        '    count=$(cat "$DRAIN_POLLS" 2>/dev/null || printf 0)\n'
        "    printf '%s\\n' $((count + 1)) > \"$DRAIN_POLLS\"\n"
        '    if [ "$count" = 0 ]; then\n'
        '      printf \'{"items":[{"metadata":{"deletionTimestamp":"now"}}]}\\n\'\n'
        "    else\n"
        "      printf '{\"items\":[]}\\n'\n"
        "    fi ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    kubectl.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    polls = tmp_path / "polls"
    run = subprocess.run(
        ["bash", str(ROOT / AWAIT_DRAINED), "system", "ufo-sandbox-proxy"],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DRAIN_POLLS": str(polls),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    assert polls.read_text().strip() == "2"


def test_await_rollout_answers_on_the_newest_replicaset(tmp_path: Path) -> None:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(KUBECTL_STUB)
    kubectl.chmod(0o755)
    calls = tmp_path / "kubectl-calls"
    replicasets = tmp_path / "replicasets.json"
    replicasets.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "metadata": {
                            "name": "other-new",
                            "annotations": {"deployment.kubernetes.io/revision": "3"},
                            "ownerReferences": [{"kind": "Deployment", "name": "ufo-other"}],
                        }
                    },
                    {
                        "metadata": {
                            "name": "ufo-serve-old",
                            "annotations": {"deployment.kubernetes.io/revision": "2"},
                            "ownerReferences": [{"kind": "Deployment", "name": "ufo-serve"}],
                        }
                    },
                    {
                        "metadata": {
                            "name": "ufo-serve-new",
                            "annotations": {"deployment.kubernetes.io/revision": "3"},
                            "ownerReferences": [{"kind": "Deployment", "name": "ufo-serve"}],
                        }
                    },
                ]
            }
        )
    )
    run = subprocess.run(
        ["bash", str(ROOT / AWAIT_ROLLOUT), "system", "ufo-serve"],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "KUBECTL_CALLS": str(calls),
            "KUBECTL_GENERATION": "3",
            "KUBECTL_OBSERVED_GENERATION": "3",
            "KUBECTL_REVISION": "3",
            "KUBECTL_REPLICAS": "2",
            "KUBECTL_REPLICASETS": str(replicasets),
            "KUBECTL_AVAILABLE": "2",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    assert (
        "deployment/ufo-serve rolled out: replicaset/ufo-serve-new has 2 replicas available"
        in run.stdout
    )
    polled = calls.read_text()
    assert "replicaset/ufo-serve-new" in polled
    assert "ufo-serve-old" not in polled
    assert "other-new" not in polled


@pytest.mark.parametrize(
    ("workflow", "job_name", "hostname", "tf_dir"),
    [
        ("deploy.yml", "rollout", "testing.ufo.ai", "infra/envs/testing"),
        ("deploy-production.yml", "deploy", "ufo.ai", "infra/envs/prod"),
    ],
)
def test_runtime_rollout_gates_the_direct_gateway_origin(
    tmp_path: Path, workflow: str, job_name: str, hostname: str, tf_dir: str
) -> None:
    job = _workflow(WORKFLOWS / workflow)["jobs"][job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    gate = _step(job_name, "Gate gateway origin", workflow)
    assert names.index("Wait for runtime rollout") < names.index("Gate gateway origin")
    assert names.index("Gate gateway origin") < names.index("Gate sandbox egress proxy TLS")
    assert gate.get("if") == (
        "github.event_name != 'pull_request'" if workflow == "deploy.yml" else None
    )
    assert gate["shell"] == "bash"
    assert "output -raw hostname" in gate["run"]

    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$ORIGIN_CALLS"\n'
        'case "$*" in\n'
        '  *"/v1/onboard/ufo"*) [ "$FAIL_PATH" != onboard ] || '
        "{ printf 'wrong\\n'; exit; }; "
        "printf 'x-ufo-session header is required.\\n' ;;\n"
        '  *"/ufo/bin/"*) [ "$FAIL_PATH" != binary ] || { printf \'404\'; exit; }; '
        "printf '200' ;;\n"
        '  */ufo) [ "$FAIL_PATH" != ufo ] || exit 1; '
        'printf \'UFO_URL="${UFO_URL:-https://%s}"\\n\' "$ORIGIN_RESPONSE_HOST" ;;\n'
        '  */fleet) [ "$FAIL_PATH" != fleet ] || exit 1; printf \'%s\\n\' "$FLEET_BODY" ;;\n'
        "esac\n"
    )
    curl.chmod(0o755)
    terraform = tmp_path / "terraform"
    terraform.write_text(
        "#!/bin/sh\n"
        '[ "$*" = "-chdir=$TF_DIR output -raw hostname" ] || exit 1\n'
        "printf '%s\\n' \"$ORIGIN_APEX_HOST\"\n"
    )
    terraform.chmod(0o755)
    calls = tmp_path / "origin-calls"
    environment = os.environ | {
        "FAIL_PATH": "",
        "FLEET_BODY": '{"craft":1}',
        "ORIGIN_APEX_HOST": hostname,
        "ORIGIN_RESPONSE_HOST": hostname,
        "ORIGIN_CALLS": str(calls),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TF_DIR": tf_dir,
    }
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", gate["run"]],
        check=True,
        env=environment,
    )
    invoked = calls.read_text().splitlines()
    assert len(invoked) == 4
    assert any(call.endswith(f"https://origin.{hostname}/v1/onboard/ufo") for call in invoked)
    assert any(call.endswith(f"https://origin.{hostname}/ufo") for call in invoked)
    assert any(
        call.endswith(f"https://origin.{hostname}/ufo/bin/x86_64-unknown-linux-musl")
        for call in invoked
    )
    assert any(call.endswith(f"https://origin.{hostname}/fleet") for call in invoked)
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", gate["run"]],
        check=True,
        env=environment | {"FLEET_BODY": '{"craft":0}'},
    )

    for failed_path in ("onboard", "ufo", "binary", "fleet"):
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", gate["run"]],
            capture_output=True,
            env=environment | {"FAIL_PATH": failed_path},
        )
        assert failed.returncode != 0

    for overrides in (
        {"ORIGIN_RESPONSE_HOST": "wrong.flyingobject.ai"},
        {"FLEET_BODY": '{"craft":"1"}'},
        {"FLEET_BODY": '{"craft":-1}'},
    ):
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", gate["run"]],
            capture_output=True,
            env=environment | overrides,
        )
        assert failed.returncode != 0, overrides


@pytest.mark.parametrize(
    ("workflow", "job_name", "environment", "revision"),
    [
        ("deploy.yml", "rollout", "testing", "$GITHUB_SHA"),
        ("deploy-production.yml", "deploy", "prod", "$TARGET_SHA"),
    ],
)
def test_runtime_rollout_verifies_the_live_billing_plan_before_origin_gates(
    workflow: str, job_name: str, environment: str, revision: str
) -> None:
    job = _workflow(WORKFLOWS / workflow)["jobs"][job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    assert names.index("Wait for runtime rollout") < names.index("Verify billing usage-export plan")
    assert names.index("Verify billing usage-export plan") < names.index("Gate gateway origin")
    step = _step(job_name, "Verify billing usage-export plan", workflow)
    assert step.get("if") == (
        "github.event_name != 'pull_request'" if workflow == "deploy.yml" else None
    )
    assert ".github/scripts/billing_export_plan_gate.sh" in step["run"]
    assert f'"$NAMESPACE" {environment} "$IMAGE_TAG" "{revision}" "$RUN_URL"' in step["run"]


def test_billing_plan_scripts_trigger_a_testing_deploy() -> None:
    selection = _step("changes", "Select deployment work")["env"]
    assert isinstance(selection, dict)
    pattern = selection["DEPLOY_PATHS_PATTERN"]
    assert isinstance(pattern, str)
    for path in (
        ".github/scripts/billing_export_plan_check.py",
        ".github/scripts/billing_export_plan_gate.sh",
    ):
        assert re.search(pattern, path)


def test_database_dashboard_reads_billing_plan_events_for_the_selected_fleet() -> None:
    dashboard = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()
    assert re.search(
        r'event_stream_definition \{\s*title = "billing usage-export plan checks"\s*'
        r'query = "check:billing_usage_export_plan env:\$env\.value"\s*\}',
        dashboard,
    )


def _check_runtime_bundle_consumes_the_sandbox_client_artifact() -> None:
    build = _step("bundle", "Build + push bundle image")
    run = build["run"]
    assert isinstance(run, str)
    assert '--client-binary "$SANDBOX_CLIENT_BINARY"' in run
    workflow = _workflow(WORKFLOWS / "deploy.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    bundle = jobs["bundle"]
    assert bundle["needs"] == ["changes", "client"]
    steps = bundle["steps"]
    assert isinstance(steps, list)
    download = next(step for step in steps if step.get("uses") == "actions/download-artifact@v4")
    assert download["if"] == "needs.client.result == 'success'"
    assert download["with"] == {
        "name": "clientbin-x86_64-unknown-linux-musl",
        "path": "servers/control/clientbin",
    }
    reuse = _step("bundle", "Reuse the pushed client binaries")
    assert reuse["if"] == "needs.client.result == 'skipped'"
    assert (
        "docker cp clientbin:/clientbin/x86_64-unknown-linux-musl "
        "servers/control/clientbin/" in reuse["run"]
    )
    assert steps.index(build) > steps.index(download)
    assert steps.index(build) > steps.index(reuse)


def _check_portal_source_maps_upload_under_the_service_and_version_the_page_records_with() -> None:
    """A fault Datadog reports against a recorded session reads as source only where the upload's
    service and version are the pair the page recorded under — so the upload names the image tag
    the template hands the page, and the service the bundle states. The maps leave the tree before
    the image is built: nothing serves a `.map`, and shipping the portal's source to a browser is
    the one way this stops being a build artifact."""
    build = _step("bundle", "Build + push bundle image")
    run = build["run"]
    assert isinstance(run, str)
    assert build["env"] == {
        "DATADOG_API_KEY": "${{ secrets.DD_API_KEY }}",
        "DATADOG_SITE": "us5.datadoghq.com",
    }
    assert "--service=ufo-portal" in run
    assert '--release-version="$IMAGE_TAG"' in run
    assert "--minified-path-prefix=/surface/web/static/assets/" in run
    assert "rm -f extensions/web/ufo_ext_web/static/assets/*.map" in run
    assert run.index("sourcemaps upload") < run.index("ufoctl bundle")
    assert run.index("ufoctl bundle") < run.index("docker build")
    assert 'load_manifests("assistant_hosted")' in run
    assert run.index("load_manifests") < run.index("docker push")

    template = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    assert '- {name: UFO_WEB_RUM_VERSION, value: "${image_tag}"}' in template
    recorder = (ROOT / "extensions" / "web" / "frontend" / "src" / "lib" / "rum.ts").read_text()
    assert 'const SERVICE = "ufo-portal";' in recorder
    build_config = (ROOT / "extensions" / "web" / "frontend" / "vite.config.ts").read_text()
    assert 'sourcemap: "hidden"' in build_config


def _check_runtime_certificates_match_ingress_tls() -> None:
    source = re.sub(
        TEMPLATE_DIRECTIVE, "", (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    )
    documents = [
        yaml.safe_load(re.sub(r"\$\{([^}]+)\}", r"\1", document))
        for document in source.split("\n---\n")
        if re.search(r"^kind: (?:Ingress|Certificate)$", document, re.MULTILINE)
    ]
    ingresses = sorted(
        (
            tls["secretName"],
            tuple(tls["hosts"]),
        )
        for document in documents
        if document["kind"] == "Ingress"
        for tls in document["spec"]["tls"]
    )
    certificates = {
        document["spec"]["secretName"]: tuple(document["spec"]["dnsNames"])
        for document in documents
        if document["kind"] == "Certificate"
    }
    assert certificates == {
        "ufo-gateway-tls": ("apex_host",),
        "ufo-ingress-tls": ("*.apex_host",),
        "ufo-serve-tls": ("shared_host",),
    }
    assert ingresses == [
        ("ufo-gateway-tls", ("apex_host",)),
        ("ufo-ingress-tls", ("*.apex_host",)),
        ("ufo-ingress-tls", ("gateway_origin_host",)),
        ("ufo-serve-tls", ("shared_host",)),
    ]


def _check_runtime_secret_consumers_roll_once_per_production_deploy() -> None:
    templates = ROOT / "infra" / "templates"
    manifests = [
        document
        for source in (
            re.sub(
                TEMPLATE_DIRECTIVE,
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
            "ufo-jobs",
            "ufo-preview",
            "ufo-sandbox-proxy",
            "ufo-serve",
        )
    }
    production = (ROOT / "infra" / "envs" / "prod" / "ufo.tf").read_text()
    testing = (ROOT / "infra" / "envs" / "testing" / "ufo.tf").read_text()
    assert len(re.findall(r"deployment_id\s+= var\.deployment_id", production)) == 2
    assert len(re.findall(r'deployment_id\s+= "testing"', testing)) == 2


def _check_the_cache_sidecar_keeps_the_proxy_probes_on_the_proxy_container() -> None:
    """With the cache enabled the sandbox-proxy pod runs two containers, and each keeps its own
    health probes — the proxy its TCP probe on the `proxy` port, the cache its own — so the sidecar
    block cannot silently detach the proxy's probes onto a container that lacks the port."""
    source = re.sub(
        TEMPLATE_DIRECTIVE, "", (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    )
    documents = [
        document
        for document in yaml.safe_load_all(re.sub(r"\$\{([^}]+)\}", r"\1", source))
        if isinstance(document, dict)
    ]
    proxy = next(
        document
        for document in documents
        if document.get("kind") == "Deployment"
        and document["metadata"]["name"] == "ufo-sandbox-proxy"
    )
    containers = {c["name"]: c for c in proxy["spec"]["template"]["spec"]["containers"]}
    assert set(containers) == {"proxy", "cache"}
    assert containers["proxy"]["readinessProbe"]["tcpSocket"]["port"] == "proxy"
    assert containers["proxy"]["livenessProbe"]["tcpSocket"]["port"] == "proxy"
    # The cache has liveness (restart a wedged daemon) but NO readiness — a cache blip must never
    # take the healthy egress proxy out of the sandbox-proxy Service.
    assert "livenessProbe" in containers["cache"]
    assert "readinessProbe" not in containers["cache"]


def _check_deployment_gate_joins_every_selected_result() -> None:
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
    }
    script = gate["run"]
    assert isinstance(script, str)
    for variable in environment:
        assert f'"${variable}"' in script


def _check_deployment_gate_accepts_only_expected_results() -> None:
    for selected, event, rollout, edge, production, access, accepted in [
        ("true", "pull_request", "success", "success", "success", "success", True),
        ("true", "push", "success", "success", "skipped", "skipped", True),
        ("false", "push", "skipped", "skipped", "skipped", "skipped", True),
        ("true", "push", "cancelled", "skipped", "skipped", "skipped", True),
        ("true", "push", "success", "cancelled", "skipped", "skipped", True),
        ("true", "pull_request", "success", "success", "skipped", "success", False),
        ("true", "pull_request", "success", "success", "success", "failure", False),
        ("true", "push", "success", "success", "success", "skipped", False),
        ("true", "push", "failure", "skipped", "skipped", "skipped", False),
        ("true", "push", "success", "failure", "skipped", "skipped", False),
        ("true", "push", "success", "success", "skipped", "success", False),
        ("false", "push", "skipped", "skipped", "success", "skipped", False),
        ("false", "push", "skipped", "skipped", "skipped", "success", False),
        ("invalid", "push", "success", "success", "skipped", "skipped", False),
    ]:
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
            "ROLLOUT_RESULT": rollout,
        }
        run = subprocess.run(["bash", "-e", "-c", script], env=os.environ | environment)
        assert (run.returncode == 0) is accepted


def test_manual_deploy_records_before_mutation_and_reports_conclusion(tmp_path: Path) -> None:
    jobs = _workflow(WORKFLOWS / "deploy-production.yml")["jobs"]
    assert isinstance(jobs, dict)
    prepare = jobs["prepare"]
    report = jobs["report"]
    assert isinstance(prepare, dict)
    assert isinstance(report, dict)
    assert prepare["outputs"]["attempt_id"] == "${{ steps.attempt.outputs.attempt_id }}"
    assert report["needs"] == ["ref", "validate", "prepare", "deploy"]
    assert report["if"] == "always()"
    record = _step("prepare", "Record the production attempt", "deploy-production.yml")
    conclusion = _step("report", "Report the production attempt", "deploy-production.yml")
    assert record["id"] == "attempt"
    assert record["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "RUN_URL": (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
        ),
        "TARGET_SHA": "${{ steps.select.outputs.target_sha }}",
    }
    assert conclusion["if"] == "always() && needs.prepare.outputs.attempt_id != ''"
    assert conclusion["env"] == {
        "ATTEMPT_ID": "${{ needs.prepare.outputs.attempt_id }}",
        "DEPLOY_RESULT": "${{ needs.deploy.result }}",
        "GH_TOKEN": "${{ github.token }}",
        "RUN_URL": (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
        ),
    }
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$GH_CALLS"\n'
        'case "$*" in\n'
        "  *'/deployments --input - --jq .id')\n"
        '    cat > "$DEPLOYMENT_PAYLOAD"\n'
        '    [ "$GH_FAIL" != deployment ] || exit 42\n'
        "    printf '123\\n'\n"
        "    ;;\n"
        "  *'/deployments/123/statuses --input -')\n"
        '    cat > "$STATUS_PAYLOAD"\n'
        '    [ "$GH_FAIL" != status ] || exit 42\n'
        "    ;;\n"
        "  *) exit 43 ;;\n"
        "esac\n"
    )
    gh.chmod(0o755)
    calls = tmp_path / "gh-calls"
    deployment = tmp_path / "deployment.json"
    github_output = tmp_path / "github-output"
    status = tmp_path / "status.json"
    environment = {
        "DEPLOYMENT_PAYLOAD": str(deployment),
        "GH_CALLS": str(calls),
        "GH_FAIL": "",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_REPOSITORY": "metalcraftai/ufo",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUN_URL": RUN_URL,
        "STATUS_PAYLOAD": str(status),
        "TARGET_SHA": "0123456789abcdef0123456789abcdef01234567",
    }
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", record["run"]],
        check=True,
        env=environment,
    )
    assert calls.read_text().splitlines() == [
        "api --method POST repos/metalcraftai/ufo/deployments --input - --jq .id",
        "api --method POST repos/metalcraftai/ufo/deployments/123/statuses --input -",
    ]
    assert json.loads(deployment.read_text()) == {
        "ref": "0123456789abcdef0123456789abcdef01234567",
        "environment": "production",
        "task": "deploy:production-attempt",
        "auto_merge": False,
        "required_contexts": [],
    }
    assert github_output.read_text() == "attempt_id=123\n"
    assert json.loads(status.read_text()) == {
        "state": "in_progress",
        "environment": "production",
        "environment_url": RUN_URL,
    }
    for failure in ("deployment", "status"):
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", record["run"]],
            capture_output=True,
            env=environment | {"GH_FAIL": failure},
        )
        assert failed.returncode != 0
    for result, state in (
        ("success", "success"),
        ("failure", "failure"),
        ("skipped", "failure"),
        ("cancelled", "failure"),
    ):
        calls.write_text("")
        subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", conclusion["run"]],
            check=True,
            env=environment | {"ATTEMPT_ID": "123", "DEPLOY_RESULT": result},
        )
        assert calls.read_text().splitlines() == [
            "api --method POST repos/metalcraftai/ufo/deployments/123/statuses --input -"
        ]
        assert json.loads(status.read_text()) == {
            "state": state,
            "environment": "production",
            "environment_url": RUN_URL,
            "auto_inactive": True,
        }
    failed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", conclusion["run"]],
        env=environment | {"ATTEMPT_ID": "123", "DEPLOY_RESULT": "failure", "GH_FAIL": "status"},
    )
    assert failed.returncode != 0


def test_successful_manual_deploy_records_the_promoted_commit(tmp_path: Path) -> None:
    record = _step("report", "Record the successful production deployment", "deploy-production.yml")
    assert record["if"] == "always() && needs.deploy.result == 'success'"
    assert record["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "RUN_URL": (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
        ),
        "TARGET_SHA": "${{ needs.prepare.outputs.target_sha }}",
    }
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'/deployments --input - --jq .id')\n"
        '    cat > "$DEPLOYMENT_PAYLOAD"\n'
        '    [ "$GH_FAIL" != deployment ] || exit 42\n'
        "    printf '456\\n'\n"
        "    ;;\n"
        "  *'/deployments/456/statuses --input -')\n"
        '    cat > "$STATUS_PAYLOAD"\n'
        '    [ "$GH_FAIL" != status ] || exit 42\n'
        "    ;;\n"
        "  *) exit 42 ;;\n"
        "esac\n"
    )
    gh.chmod(0o755)
    deployment = tmp_path / "deployment.json"
    status = tmp_path / "status.json"
    environment = {
        "DEPLOYMENT_PAYLOAD": str(deployment),
        "GH_FAIL": "",
        "GITHUB_REPOSITORY": "metalcraftai/ufo",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUN_URL": RUN_URL,
        "STATUS_PAYLOAD": str(status),
        "TARGET_SHA": "0123456789abcdef0123456789abcdef01234567",
    }
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", record["run"]],
        check=True,
        env=environment,
    )
    assert json.loads(deployment.read_text()) == {
        "ref": "0123456789abcdef0123456789abcdef01234567",
        "environment": "production",
        "task": "deploy:production",
        "auto_merge": False,
        "required_contexts": [],
    }
    assert json.loads(status.read_text()) == {
        "state": "success",
        "environment": "production",
        "environment_url": RUN_URL,
        "auto_inactive": True,
    }
    for failure in ("deployment", "status"):
        failed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", record["run"]],
            env=environment | {"GH_FAIL": failure},
        )
        assert failed.returncode != 0


def test_every_main_deploy_conclusion_reaches_datadog(tmp_path: Path) -> None:
    step = _step("deploy", "Report the deploy conclusion to Datadog")
    assert step["if"] == (
        "always() && github.ref_name == 'main' && "
        "needs.rollout.result != 'cancelled' && needs.edge.result != 'cancelled'"
    )
    assert step["env"] == {
        "DD_CHECK_URL": "https://api.us5.datadoghq.com/api/v1/check_run",
        "DEPLOY_CHECK": "ufo.deploy.main",
        "DD_STATUS_OK": "0",
        "DD_STATUS_CRITICAL": "2",
        "TESTING_RESULT": "${{ needs.rollout.result }}",
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

    posted_to, testing_failed = _report(tmp_path, "failure", "skipped")
    assert urlparse(posted_to).hostname == urlparse(api_urls.pop()).hostname
    assert testing_failed["message"] == RUN_URL
    assert testing_failed["status"] == DATADOG_STATUS_CRITICAL

    _, succeeded = _report(tmp_path, "success")
    assert succeeded["message"] == RUN_URL
    assert succeeded["status"] == DATADOG_STATUS_OK

    _, edge_failed = _report(tmp_path, "success", "failure")
    assert edge_failed["status"] == DATADOG_STATUS_CRITICAL

    _, edge_skipped = _report(tmp_path, "success", "skipped")
    assert edge_skipped["status"] == DATADOG_STATUS_CRITICAL

    _, prod_succeeded = _report_production(tmp_path, "success")
    assert prod_succeeded["message"] == RUN_URL
    assert prod_succeeded["status"] == DATADOG_STATUS_OK
    _, prod_failed = _report_production(tmp_path, "failure")
    assert prod_failed["status"] == DATADOG_STATUS_CRITICAL
    _, validate_failed = _report_production(
        tmp_path,
        "skipped",
        validate_result="failure",
        prepare_result="skipped",
    )
    assert validate_failed["status"] == DATADOG_STATUS_CRITICAL
    _, prepare_failed = _report_production(
        tmp_path,
        "skipped",
        prepare_result="failure",
    )
    assert prepare_failed["status"] == DATADOG_STATUS_CRITICAL


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

    if environment == "testing":
        _, failed = _report(tmp_path, "failure", "skipped")
        _, succeeded_report = _report(tmp_path, "success")
    else:
        _, failed = _report_production(tmp_path, "failure")
        _, succeeded_report = _report_production(tmp_path, "success")
    assert failed["check"] == check
    assert succeeded_report["check"] == check
    assert scope in failed["tags"]
    assert scope in succeeded_report["tags"]
    assert grouping in _facets(failed)
    assert failed["status"] == DATADOG_STATUS_CRITICAL
    assert succeeded_report["status"] == DATADOG_STATUS_OK


def _check_every_monitor_notifies_a_reachable_handle() -> None:
    for environment in DEPLOY_ENVIRONMENTS:
        monitors = re.findall(
            r'resource "datadog_monitor" "(\w+)"', MONITORS[environment].read_text()
        )
        assert monitors
        for monitor in monitors:
            message = _monitor_attribute(monitor, "message", environment)
            assert re.search(HANDLE, message), monitor


def _check_the_source_sync_monitor_watches_the_check_the_reporters_submit() -> None:
    """The two numbers are the whole behaviour asked of this monitor: a single failed run is one
    CRITICAL out of the last two statuses and pages nobody, the second consecutive one alerts, and a
    single OK clears it. The grouping is the tags `_check_tags` submits, down to the source row —
    two workspaces that each connect Slack run one provider stream, and a group over both would let
    the healthy row's OK clear the failing row's alert. The check name is the one the fleet reports
    under: a monitor over any other name would read as permanently green."""
    for environment in DEPLOY_ENVIRONMENTS:
        query = _monitor_attribute("source_sync_failed", "query", environment)
        parsed = re.fullmatch(
            r'"([\w.]+)"\.over\("([^"]+)"\)\.by\(([^)]+)\)\.last\((\d+)\)\.count_by_status\(\)',
            query,
        )
        assert parsed
        assert _monitor_attribute("source_sync_failed", "type", environment) == "service check"
        assert parsed.group(1) == f"ufo.{SOURCE_SYNC_CHECK}"
        assert parsed.group(2) == f"env:{environment}"
        assert re.findall(r'"(\w+)"', parsed.group(3)) == ["provider", "stream", "source_id"]
        assert _monitor_attribute("source_sync_failed", "critical", environment) == "2"
        assert _monitor_attribute("source_sync_failed", "ok", environment) == "1"
        assert int(parsed.group(4)) >= 2
        assert _monitor_attribute("source_sync_failed", "notify_no_data", environment) == "false"
        message = _monitor_attribute("source_sync_failed", "message", environment)
        assert "{{provider.name}}" in message
        assert "{{stream.name}}" in message
        assert "{{source_id.name}}" in message


def _check_the_source_sync_monitor_reports_both_transitions() -> None:
    """A stream that cannot sync holds CRITICAL until somebody fixes it, so the alert repeats hourly
    rather than scrolling away once. The clear is a message of its own: an operator who reads the
    channel has no other place to learn the stream came back, and the failure text would read as a
    second incident if it were sent again on recovery. The handles sit outside both blocks, so both
    transitions reach the same targets."""
    for environment in DEPLOY_ENVIRONMENTS:
        assert _monitor_attribute("source_sync_failed", "renotify_interval", environment) == "60"
        message = _monitor_attribute("source_sync_failed", "message", environment)
        blocks = dict(re.findall(r"{{#(is_alert|is_recovery)}}(.*?){{/\1}}", message))
        assert set(blocks) == {"is_alert", "is_recovery"}
        assert "still failing" in blocks["is_alert"]
        assert "still failing" not in blocks["is_recovery"]
        assert re.search(HANDLE, message)
        for block in blocks.values():
            assert not re.search(HANDLE, block)


def _check_the_fleet_submits_the_stream_check_to_the_datadog_its_monitors_read() -> None:
    """The monitor and the reporter are configured in different files and neither fails when they
    disagree: a check submitted to another Datadog site, or tagged with another env, leaves the
    monitor's scope holding no status for any stream — which reads exactly like a fleet whose
    streams all sync. So the fleet's own intake and env tag are held against the provider the
    monitors talk to and the intake the deploy reporter already uses, and against the pod's key."""
    for environment in DEPLOY_ENVIRONMENTS:
        config = (ROOT / "infra" / "envs" / environment / "ufo.tf").read_text()
        check_url = re.search(r'datadog_check_url = "(\S+)"', config)
        api_url = re.search(
            r'^  api_url += +"(\S+)"$', MONITORS[environment].read_text(), re.MULTILINE
        )
        assert check_url and api_url
        assert urlparse(check_url.group(1)).hostname == urlparse(api_url.group(1)).hostname
        deploy_reporter = _step("deploy", "Report the deploy conclusion to Datadog")["env"]
        assert isinstance(deploy_reporter, dict)
        assert check_url.group(1) == deploy_reporter["DD_CHECK_URL"]
        environment_tag = re.search(r'datadog_env = "(\w+)"', config)
        assert environment_tag
        assert environment_tag.group(1) == environment
        hosted = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
        assert "secretKeyRef: {name: datadog-api-key, key: DD_API_KEY}" in hosted
        assert '- {name: DD_SITE, value: "${rum_site}"}' in hosted


def _check_surface_listener_park_monitor_consumes_the_reported_metric() -> None:
    for environment in DEPLOY_ENVIRONMENTS:
        assert _monitor_attribute("surface_listener_parked", "query", environment) == (
            "sum(last_15m):sum:ufo.surface_listener_parked_total"
            f"{{env:{environment}}} by {{surface}}.as_count() >= 1"
        )
        message = _monitor_attribute("surface_listener_parked", "message", environment)
        assert "{{surface.name}}" in message
        assert _monitor_attribute("surface_listener_parked", "critical", environment) == "1"
        assert _monitor_attribute(
            "surface_listener_parked", "require_full_window", environment
        ) == ("false")


def _check_the_job_failure_monitor_consumes_the_reported_counter() -> None:
    """The counter `fire` emits on a raised handler is the only trace a failed job leaves — it
    acknowledges nothing and writes nothing — so a monitor over any other name reads as a fleet
    whose jobs all run. The grouping is the dispatcher's key, which is what names the failing job;
    the threshold is reachable by one workspace on a per-minute job inside the window and by no
    blip either fleet recorded."""
    for environment in DEPLOY_ENVIRONMENTS:
        assert _monitor_attribute("job_failed", "query", environment) == (
            f"sum(last_30m):sum:ufo.{JOB_FAILED_METRIC}"
            f"{{env:{environment}}} by {{job}}.as_count() >= 20"
        )
        assert _monitor_attribute("job_failed", "critical", environment) == "20"
        assert _monitor_attribute("job_failed", "require_full_window", environment) == "false"
        message = _monitor_attribute("job_failed", "message", environment)
        assert "{{job.name}}" in message
        assert "jobs.failed" in message


def _check_a_sparse_counters_alert_can_clear_itself() -> None:
    for environment in DEPLOY_ENVIRONMENTS:
        for monitor in [
            "db_tx_unavailable",
            "db_pool_exhausted",
            "surface_listener_parked",
            "job_failed",
        ]:
            assert _monitor_attribute(monitor, "require_full_window", environment) == "false"


def _check_testing_owns_one_database_and_model_board_for_both_fleets() -> None:
    """Both boards read either fleet through `$env`, so the prod root declares no board at all. One
    it declared would be a second copy under its own id rather than the prod view of this one."""
    assert not (ROOT / "infra" / "envs" / "prod" / "dashboards.tf").exists()
    dashboards = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()
    assert re.findall(r'resource "datadog_dashboard" "(\w+)"', dashboards) == [
        "database",
        "model_latency",
        "model_rounds",
        "turns",
        "coding_quality",
        "prompt_cache",
        "evals",
        "sandbox_health",
        "product",
    ]
    assert "env:testing" not in dashboards
    assert "env:prod" not in dashboards
    assert "first three graphs" in dashboards
    assert re.findall(r'title\s+=\s+"([^"]+)"', dashboards)[:4] == [
        "ufo database",
        "transactions that never opened (client side)",
        "how long a transaction waited for a connection",
        "pools exhausted at their ceiling (client side)",
    ]
    assert "sum:ufo.db_tx_unavailable_total{$env} by {path,error_class}.as_count()" in dashboards
    assert "p95:ufo.db_tx_acquire_ms{$env} by {path}" in dashboards
    assert "p99:ufo.db_tx_acquire_ms{$env} by {path}" in dashboards
    assert "sum:ufo.db_pool_exhausted_total{$env} by {path}.as_count()" in dashboards
    assert "p50:ufo.model_provider_start_ms{$env} by {provider,model,profile}" in dashboards
    assert "p95:ufo.model_first_visible_event_ms{$env} by {provider,model,profile}" in dashboards
    assert "sum:ufo.model_round_active{$env} by {provider,model,profile}" in dashboards
    assert (
        "sum:ufo.model_provider_retry_total{$env} by {provider,model,kind}.as_count()" in dashboards
    )
    assert "first token wait" not in dashboards


def _check_the_rds_widgets_switch_fleet_on_the_instance_identifier() -> None:
    """CloudWatch reports the instance's own tags, where the environment reads `ufo-testing` on one
    fleet and `prod` on the other, so no RDS query can ride `$env`. The presets are what keep one
    selection moving both variables together."""
    dashboards = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()
    assert re.findall(r"avg:aws\.rds\.\w+\{([^}]*)\}", dashboards) == ["$dbinstance"] * 7
    assert "avg:aws.rds.cpucredit_balance{$dbinstance}" in dashboards
    assert dashboards.count("template_variable_preset {") == 4
    assert re.findall(r"available_values = \[([^]]*)\]", dashboards) == [
        '"testing", "prod"',
        '"ufo-testing-postgres", "prod-postgres"',
        '"testing", "prod"',
        '"prod", "testing"',
        '"ufo-testing", "dev"',
        '"sweep", "smoke"',
        '"claude-opus-5", "z-ai/glm-5.3", "z-ai/glm-5.3-flash"',
        '"testing", "prod"',
        '"prod", "testing"',
    ]


def _check_the_model_rounds_board_reads_failures_from_all_three_sources() -> None:
    """The metrics see every round, the spans keep the class the metric folds onto `other`, and
    OpenRouter's LLM Observability record is keyed by API key name rather than fleet, so the `prod`
    and `testing` presets carry the key that serves each fleet."""
    dashboard = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()
    board = dashboard.split('resource "datadog_dashboard" "model_rounds" {', 1)[1].split(
        'resource "datadog_dashboard" "turns" {', 1
    )[0]
    scope = "{$env,$provider,$model,$profile"
    assert board.count("count:ufo.model_round_ms" + scope) == 16
    assert (
        "100 * count:ufo.model_round_ms{$env,$provider,$model,$profile,error_class:*} "
        "/ count:ufo.model_round_ms{$env,$provider,$model,$profile}"
    ) in board
    assert "count:ufo.model_round_ms{$env}" not in board
    assert board.count('data_source = "spans"') == 1
    assert board.count('data_source  = "trace_stream"') == 1
    assert board.count('data_source  = "llm_observability_stream"') == 1
    assert board.count("status:error @ufo.provider:$provider.value") == 2
    assert 'facet = "@ufo.error_class"' in board
    assert '@tags:\\"api_key_name:$api_key.value\\"' in board
    presets = board.split("template_variable_preset {")[1:]
    assert [
        (
            re.search(r'name = "(\w+)"', preset).group(1),
            re.search(r'name   = "api_key"\s+values = \["([^"]+)"\]', preset).group(1),
        )
        for preset in presets
    ] == [("prod", "ufo-testing"), ("testing", "dev")]


def _check_eval_dashboard_scopes_every_score_to_the_target_model() -> None:
    dashboard = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()

    assert 'name             = "target_model"' in dashboard
    assert 'defaults         = ["claude-opus-5"]' in dashboard
    assert dashboard.count("ufo.evals.cases_passed{$mode,$target_model}") == 3
    assert dashboard.count("ufo.evals.cases_scored{$mode,$target_model}") == 3
    assert "ufo.evals.cases_passed{$mode}" not in dashboard
    assert "ufo.evals.cases_scored{$mode}" not in dashboard


def _check_prompt_cache_dashboard_consumes_round_gap_and_ttl_metrics() -> None:
    dashboard = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()
    assert 'resource "datadog_dashboard" "prompt_cache"' in dashboard
    assert "ufo.model_cache_round_total" in dashboard
    assert "ufo.model_cache_tokens_total" in dashboard
    assert "ufo.model_first_visible_event_ms" in dashboard
    assert "by {round,kind,conversation_ttl}" in dashboard
    assert "by {result,gap}" in dashboard
    assert "gap:5m_1h,kind:cache_read" in dashboard
    assert 'name     = "provider"' in dashboard
    assert (
        "sum:ufo.model_cache_tokens_total"
        "{$env,$profile,provider:anthropic,round:first,gap:5m_1h,kind:cache_read}" in dashboard
    )
    assert (
        "sum:ufo.model_cache_tokens_total"
        "{$env,$profile,provider:anthropic,kind:cache_write_1h}" in dashboard
    )
    assert "value      = 65.2" in dashboard


def _check_the_product_board_counts_a_workspace_out_of_one_census_bucket() -> None:
    """One census tick counts every workspace once, so a workspace count is right only where the
    bucket is the census period: a rate scaled by that period reads a narrower bucket as a whole
    period, and a bucket Datadog sizes for itself holds as many ticks as it is wide. Every query on
    this board therefore pins the rollup to the census period, every toplist ranks by the same `max`
    the big numbers take, and every named query reduces by that `max` as well, so the funnel and the
    number above it cannot disagree. A query with no aggregator reduces by the Datadog default over
    the whole board window, which reads the filling edge bucket and understates a growing count."""
    dashboard = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()
    census = re.findall(
        r'(?:q|query)\s+=\s+"([^"]*ufo\.product_(?:stage|attach|active_member_2d_7d)_total[^"]*)"',
        dashboard,
    )

    assert f"product_census_seconds = {PRODUCT_CENSUS_SECONDS}" in dashboard
    assert len(census) == 13
    assert "as_rate()" not in dashboard
    for query in census:
        assert ".as_count().rollup(sum, ${local.product_census_seconds})" in query
        assert "${local.product_census_seconds} *" not in query
    ranked = [query for query in census if query.startswith("top(")]
    assert len(ranked) == 5
    assert all("'max', 'desc')" in query for query in ranked)
    product = dashboard.split('resource "datadog_dashboard" "product" {', 1)[1]
    funnel_queries = product.split("metric_query {")[1:]
    assert len(funnel_queries) == 1
    assert all(block.splitlines()[3].strip() == 'aggregator = "max"' for block in funnel_queries)


def _check_every_product_card_shows_its_count_and_history() -> None:
    dashboard = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()

    product = dashboard.split('resource "datadog_dashboard" "product" {', 1)[1]

    assert product.count("query_value_definition {") == 7
    assert product.count('aggregator = "max"') == 8
    assert product.count("timeseries_background {") == 7
    assert product.count('timeseries_background {\n        type = "bars"\n      }') == 7
    assert "change_definition {" not in product
    for stage in ("seated", "connector", "invited", "chatted", "active_7d", "paid"):
        assert f"stage:{stage}" in product
    assert (
        "sum:ufo.product_active_member_2d_7d_total{$env}.as_count().rollup("
        "sum, ${local.product_census_seconds})"
    ) in product


def _check_the_product_funnel_shows_count_and_relative_bar() -> None:
    dashboard = (ROOT / "infra" / "envs" / "testing" / "dashboards.tf").read_text()

    product = dashboard.split('resource "datadog_dashboard" "product" {', 1)[1]
    funnel = product.split('title = "workspaces by funnel stage"', 1)[1].split("widget {", 1)[0]

    assert 'formula_expression = "reached"' in funnel
    assert 'alias              = "workspaces"' in funnel
    assert 'cell_display_mode  = "bar"' in funnel
    assert "denom" not in funnel


def _check_database_capacity_monitors() -> None:
    for environment, monitor, query, critical, warning in [
        (
            "testing",
            "db_memory_low",
            "min(last_15m):avg:aws.rds.freeable_memory"
            "{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 209715200",
            "209715200",
            "419430400",
        ),
        (
            "testing",
            "db_connections_high",
            "avg(last_15m):avg:aws.rds.database_connections"
            "{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 380",
            "380",
            "350",
        ),
        (
            "prod",
            "db_memory_low",
            "min(last_15m):avg:aws.rds.freeable_memory"
            "{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 419430400",
            "419430400",
            "838860800",
        ),
        (
            "prod",
            "db_connections_high",
            "avg(last_15m):avg:aws.rds.database_connections"
            "{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 380",
            "380",
            "350",
        ),
    ]:
        assert _monitor_attribute(monitor, "query", environment) == query
        assert _monitor_attribute(monitor, "critical", environment) == critical
        assert _monitor_attribute(monitor, "warning", environment) == warning
        assert _monitor_attribute(monitor, "evaluation_delay", environment) == "900"
        assert _monitor_attribute(monitor, "tags", environment) == (
            f'["env:{environment}", "managed-by:terraform"]'
        )


def _check_runtime_fleets_fit_the_database_connection_budget() -> None:
    per_runtime = (
        RUNTIME_DATABASE_LOOPS * (POOL_SIZE + MAX_OVERFLOW + OWNER_POOL_SIZE + OWNER_MAX_OVERFLOW)
        + DBOS_SYSTEM_DATABASE_POOL_SIZE
        + DBOS_CLIENT_POOL_SIZE
    )
    for environment in DEPLOY_ENVIRONMENTS:
        source = (ROOT / "infra" / "envs" / environment / "ufo.tf").read_text()
        replicas = {
            fleet: int(count)
            for fleet, count in re.findall(
                r"^    (serve|jobs)_replicas += +(\d+)$", source, re.MULTILINE
            )
        }
        assert replicas == {"serve": 2, "jobs": 2}
        ceiling = NON_RUNTIME_DATABASE_CONNECTIONS + sum(replicas.values()) * per_runtime
        assert ceiling == 348
        assert ceiling < int(_monitor_attribute("db_connections_high", "warning", environment))
        if environment == "testing":
            assert ceiling < TESTING_DATABASE_CONNECTIONS


def _check_edge_doors_use_separate_environment_origins() -> None:
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
        "ufo.ai": "https://origin.ufo.ai",
        "testing.ufo.ai": "https://origin.testing.ufo.ai",
    }
    hosted = (ROOT / "infra" / "templates" / "hosted.yaml.tpl").read_text()
    assert "${apex_host},${gateway_origin_host}" in hosted
    assert "hosts: [${apex_host}]\n      secretName: ufo-gateway-tls" in hosted
    assert "hosts: [${gateway_origin_host}]\n      secretName: ufo-ingress-tls" in hosted
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
          - path: /v1/onboard
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /ufo
            pathType: Exact
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /ufo/bin
            pathType: Prefix
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


def _check_edge_worker_artifact_substitutes_every_placeholder() -> None:
    module = ROOT / "infra" / "modules" / "edge"
    terraform = (module / "main.tf").read_text()
    assert re.search(r'landing_html\s+= file\("\$\{path\.module\}/landing\.html"\)', terraform)
    assert not re.search(r"__[A-Z_]+__", (module / "landing.html").read_text())
    worker = (module / "worker.js").read_text()
    harness = (module / "harness.mjs").read_text()
    substituted = {
        "__FAVICON_DARK_SVG__": "var.favicon_dark_svg",
        "__FAVICON_SVG__": "var.favicon_svg",
        "__LANDING_HTML__": "local.landing_html",
        "__PRIVACY_HTML__": "local.privacy_html",
        "__TERMS_HTML__": "local.terms_html",
    }
    assert set(re.findall(r'"(__[A-Z_]+__)"', worker)) == set(substituted)
    for placeholder, value in substituted.items():
        assert worker.count(f'"{placeholder}"') == 1
        assert re.search(rf'"\\"{placeholder}\\"",\n\s+jsonencode\({re.escape(value)}\)', terraform)
        assert harness.count(f"'\"{placeholder}\"'") == 1


def _check_both_door_gates_grep_the_join_door_the_card_prints() -> None:
    worker = (ROOT / "infra" / "modules" / "edge" / "worker.js").read_text()
    apex = re.search(r'const APEX = "([^"]+)"', worker)
    prefix = re.search(r'const JOIN_PREFIX = "([^"]+)"', worker)
    assert apex and prefix
    assert "const JOIN_URL = `${APEX}${JOIN_PREFIX}ufo`" in worker
    assert "  Sign up: ${JOIN_URL}" in worker
    join = f"{apex.group(1)}{prefix.group(1)}ufo"
    for job, name, workflow in (
        ("edge", "Gate testing door", "deploy.yml"),
        ("deploy", "Gate production door", "deploy-production.yml"),
    ):
        script = _step(job, name, workflow)["run"]
        assert isinstance(script, str)
        assert f'grep -F "{join}"' in script


def _check_the_client_target_set_is_one_set_everywhere() -> None:
    def matrix_targets(workflow: str, job: str) -> set[str]:
        jobs = _workflow(WORKFLOWS / workflow)["jobs"]
        assert isinstance(jobs, dict)
        client = jobs[job]
        assert isinstance(client, dict)
        return {entry["target"] for entry in client["strategy"]["matrix"]["include"]}

    gateway = (ROOT / "servers" / "control" / "src" / "gateway.rs").read_text()
    literal = re.search(r"CLIENT_TARGETS: &\[&str\] = &\[(.*?)\];", gateway, re.DOTALL)
    assert literal
    served = set(re.findall(r'"([^"]+)"', literal.group(1)))
    assert matrix_targets("client.yml", "build") == served
    assert matrix_targets("deploy.yml", "client") == served


def _check_deploy_workflow_static_contract() -> None:
    for check in (
        _check_platform_addons_wait_for_the_load_balancer_webhook,
        _check_webhook_addons_install_atomically,
        _check_every_main_push_triggers_deployment,
        _check_every_pull_request_has_one_deployment_gate,
        _check_pull_request_plans_active_deployment_inputs,
        _check_testing_deploy_writes_provider_credentials_before_apply,
        _check_testing_deploy_seeds_metric_names_before_apply,
        _check_authorization_expansions_co_deploy_but_contractions_split,
        _check_plans_run_only_for_selected_deployment_inputs,
        _check_production_edge_preserves_the_promoted_workspace,
        _check_production_shared_edge_uses_current_main,
        _check_pull_requests_guard_the_production_edge_plan,
        _check_pull_requests_plan_production_foundation_without_applying,
        _check_production_vcpu_reservation_matches_the_node_group,
        _check_production_nlb_reservation_matches_the_services,
        _check_hosted_runtime_receives_the_selected_sandbox_template,
        _check_hosted_serve_attests_the_deployed_bundle,
        _check_rollout_plan_pins_the_selected_artifacts,
        _check_only_testing_owns_account_global_resources,
        _check_production_deploy_role_trusts_only_the_main_production_workflow,
        _check_production_deploy_consumes_the_protected_role_and_artifacts,
        _check_production_secrets_fail_before_aws_changes,
        _check_production_prepare_applies_access_before_deploy,
        _check_production_deploy_rejects_missing_inputs_before_role_assumption,
        _check_production_deploy_applies_guarded_foundation_then_runtime,
        _check_the_template_publish_takes_the_client_binary_a_pipeline_already_built,
        _check_hosted_namespaces_hold_rollouts_until_nlb_targets_are_ready,
        _check_sandbox_proxy_nlb_routes_across_all_enabled_zones,
        _check_runtime_bundle_consumes_the_sandbox_client_artifact,
        _check_portal_source_maps_upload_under_the_service_and_version_the_page_records_with,
        _check_runtime_certificates_match_ingress_tls,
        _check_runtime_secret_consumers_roll_once_per_production_deploy,
        _check_the_cache_sidecar_keeps_the_proxy_probes_on_the_proxy_container,
        _check_deployment_gate_joins_every_selected_result,
        _check_testing_owns_one_database_and_model_board_for_both_fleets,
        _check_the_rds_widgets_switch_fleet_on_the_instance_identifier,
        _check_the_model_rounds_board_reads_failures_from_all_three_sources,
        _check_eval_dashboard_scopes_every_score_to_the_target_model,
        _check_prompt_cache_dashboard_consumes_round_gap_and_ttl_metrics,
        _check_the_product_board_counts_a_workspace_out_of_one_census_bucket,
        _check_every_product_card_shows_its_count_and_history,
        _check_the_product_funnel_shows_count_and_relative_bar,
        _check_edge_doors_use_separate_environment_origins,
        _check_edge_worker_artifact_substitutes_every_placeholder,
        _check_both_door_gates_grep_the_join_door_the_card_prints,
        _check_the_client_target_set_is_one_set_everywhere,
    ):
        check()


def test_deploy_workflow_sync_contract() -> None:
    for check in (
        _check_database_storage_monitor_tracks_allocation,
        _check_flag_resources_keep_their_refresh_address,
        _check_edge_writers_share_deploy_concurrency,
        _check_saved_plans_reject_destructive_changes,
        _check_mutating_plans_lock_state_and_preserve_inputs,
        _check_apply_uses_the_guarded_plan,
        _check_proxy_gate_dials_the_rolled_proxy_with_the_shared_ca,
        _check_runtime_rollout_drains_before_the_proxy_gate,
        _check_runtime_apply_waits_for_prior_drains,
        _check_deployment_gate_accepts_only_expected_results,
        _check_every_monitor_notifies_a_reachable_handle,
        _check_the_source_sync_monitor_watches_the_check_the_reporters_submit,
        _check_the_source_sync_monitor_reports_both_transitions,
        _check_the_fleet_submits_the_stream_check_to_the_datadog_its_monitors_read,
        _check_surface_listener_park_monitor_consumes_the_reported_metric,
        _check_the_job_failure_monitor_consumes_the_reported_counter,
        _check_a_sparse_counters_alert_can_clear_itself,
        _check_database_capacity_monitors,
        _check_runtime_fleets_fit_the_database_connection_budget,
        _check_deploy_workflow_static_contract,
    ):
        check()
