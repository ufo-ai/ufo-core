import re
from pathlib import Path

from ufo.serve import RESERVED_HOST_PREFIXES

HOSTED_TEMPLATE = Path(__file__).resolve().parents[2] / "infra/templates/hosted.yaml.tpl"
IAM_MODULE = Path(__file__).resolve().parents[2] / "infra/modules/platform/iam.tf"
CLUSTER_SERVICES_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "infra/templates/cluster-services.yaml.tpl"
)
PLATFORM_SECRETS = Path(__file__).resolve().parents[2] / "infra/modules/platform/secrets.tf"
PLATFORM_OUTPUTS = Path(__file__).resolve().parents[2] / "infra/modules/platform/outputs.tf"
APP_S3_MODULE = IAM_MODULE.read_text().split('module "irsa_app_s3" {', maxsplit=1)[1]
APP_S3_POLICY = (
    IAM_MODULE.read_text()
    .split('data "aws_iam_policy_document" "app_s3" {', maxsplit=1)[1]
    .split('data "aws_iam_policy_document" "sandbox_fs_trust"', maxsplit=1)[0]
)
SANDBOX_PROXY_POLICY = (
    IAM_MODULE.read_text()
    .split('data "aws_iam_policy_document" "sandbox_proxy" {', maxsplit=1)[1]
    .split('resource "aws_iam_policy" "sandbox_proxy"', maxsplit=1)[0]
)
SANDBOX_PROXY_IRSA = IAM_MODULE.read_text().split('module "irsa_sandbox_proxy" {', maxsplit=1)[1]
PROXY_DEPLOYMENT = (
    HOSTED_TEMPLATE.read_text()
    .split("kind: Deployment\nmetadata:\n  name: ufo-sandbox-proxy", maxsplit=1)[1]
    .split("---", maxsplit=1)[0]
)
SERVE_DEPLOYMENT = (
    HOSTED_TEMPLATE.read_text()
    .split("kind: Deployment\nmetadata:\n  name: ufo-serve", maxsplit=1)[1]
    .split("---", maxsplit=1)[0]
)
TESTING_CONFIG = Path(__file__).resolve().parents[2] / "infra/envs/testing/ufo.tf"
PROD_CONFIG = Path(__file__).resolve().parents[2] / "infra/envs/prod/ufo.tf"


def test_app_s3_trusts_serve_in_every_ufo_namespace() -> None:
    assert 'assume_role_condition_test = "StringLike"' in APP_S3_MODULE
    assert 'namespace_service_accounts = ["ufo-*:ufo-serve"]' in APP_S3_MODULE
    assert "sts:AssumeRole" not in APP_S3_POLICY


def test_sandbox_proxy_identity_can_only_assume_the_mount_role() -> None:
    assert 'actions   = ["sts:AssumeRole"]' in SANDBOX_PROXY_POLICY
    assert "role/${local.name}-sandbox-fs" in SANDBOX_PROXY_POLICY
    assert "s3:" not in SANDBOX_PROXY_POLICY
    assert 'namespace_service_accounts = ["ufo-*:ufo-sandbox-proxy"]' in SANDBOX_PROXY_IRSA
    assert "identifiers = [module.irsa_sandbox_proxy.iam_role_arn]" in IAM_MODULE.read_text()
    proxy_output = PLATFORM_OUTPUTS.read_text().split(
        'output "sandbox_proxy_role_arn" {', maxsplit=1
    )[1]
    assert 'role/${local.name}-sandbox-proxy"' in proxy_output
    assert "module.irsa_sandbox_proxy.iam_role_arn" not in proxy_output


def test_hosted_serve_receives_the_bedrock_region() -> None:
    assert '- {name: AWS_REGION, value: "${region}"}' in HOSTED_TEMPLATE.read_text()


def test_hosted_serve_rolls_all_replacements_before_draining() -> None:
    assert "maxSurge: 100%" in SERVE_DEPLOYMENT
    assert "maxUnavailable: 0" in SERVE_DEPLOYMENT
    assert "terminationGracePeriodSeconds: ${termination_grace_period_seconds}" in SERVE_DEPLOYMENT
    assert 'command: [sleep, "${prestop_seconds}"]' in SERVE_DEPLOYMENT


def test_hosted_proxy_rolls_all_replacements_and_drains_connections() -> None:
    assert "maxSurge: 100%" in PROXY_DEPLOYMENT
    assert "maxUnavailable: 0" in PROXY_DEPLOYMENT
    assert "terminationGracePeriodSeconds: ${termination_grace_period_seconds}" in PROXY_DEPLOYMENT
    assert 'command: [sleep, "${prestop_seconds}"]' in PROXY_DEPLOYMENT


def test_hosted_shutdown_grace_is_environment_specific() -> None:
    """The env local is the single source for each drain window: the rendered `[serve]` config
    interpolates the local (never a second literal), and the pod's termination grace is computed
    from both sequential shutdown phases — so no value can drift from its enforcement."""
    testing = TESTING_CONFIG.read_text()
    prod = PROD_CONFIG.read_text()
    assert "  graceful_shutdown_seconds = 0\n" in testing
    assert "  graceful_shutdown_seconds = 600\n" in prod
    for config in (testing, prod):
        assert "  prestop_seconds           = 10\n" in config
        assert "  request_shutdown_seconds  = 30\n" in config
        assert "prestop_seconds                  = local.prestop_seconds" in config
        assert "request_shutdown_seconds = ${local.request_shutdown_seconds}" in config
        assert "graceful_shutdown_seconds = ${local.graceful_shutdown_seconds}" in config
        assert (
            "termination_grace_period_seconds = local.prestop_seconds + "
            "local.request_shutdown_seconds + local.graceful_shutdown_seconds + 60"
        ) in config


def test_hosted_serve_receives_the_bedrock_mantle_api_key() -> None:
    assert '"bedrock-api-key"' in PLATFORM_SECRETS.read_text()
    assert (
        "{secretKey: AWS_BEARER_TOKEN_BEDROCK, "
        "remoteRef: {key: ${secret_api_keys}, property: bedrock-api-key}}"
        in CLUSTER_SERVICES_TEMPLATE.read_text()
    )


def test_hosted_serve_receives_the_exa_api_key() -> None:
    assert (
        "{secretKey: EXA_API_KEY, "
        "remoteRef: {key: ${secret_api_keys}, property: exa-api-key}}"
        in CLUSTER_SERVICES_TEMPLATE.read_text()
    )


def test_app_host_ingress_routes_login_to_gateway_and_product_to_serve() -> None:
    """The shared app host fronts both the onboarding gateway and the serve fleet behind one
    ingress, so the sign-in flow is same-origin with the product it deposits members into. The
    browser sign-in endpoints (`/login`, the `/v1/onboard` wire, the `/ufo` install script) route
    to `ufo-gateway`; everything else (`/`, and thus `/surface/*`, the OAuth callback, artifacts)
    routes to `ufo-serve`. The two path namespaces are disjoint by nginx longest-prefix; pinning
    the split here keeps a future edit — or a serve route grabbing a reserved prefix — from
    silently shadowing the gateway."""
    blocks = HOSTED_TEMPLATE.read_text().split("kind: Ingress")
    serve_ingress = next(b for b in blocks if "name: ufo-serve" in b.split("---", 1)[0])
    pattern = r"- path: (\S+)\s+pathType: (\S+).*?name: (ufo-\S+)"
    routes = re.findall(pattern, serve_ingress, re.DOTALL)
    routing = {path: (path_type, service) for path, path_type, service in routes}
    expected = {prefix: ("Prefix", "ufo-gateway") for prefix in RESERVED_HOST_PREFIXES}
    expected["/"] = ("Prefix", "ufo-serve")
    assert routing == expected


def test_hosted_proxy_receives_the_composio_broker_key() -> None:
    """The shared egress proxy forwards sentinel CLI requests through Composio's proxy-execute, so
    its pod needs the broker key exactly as it needs the model keys it swaps."""
    assert "name: COMPOSIO_API_KEY" in PROXY_DEPLOYMENT
    assert "secretKeyRef: {name: ufo-platform-secrets, key: COMPOSIO_API_KEY}" in PROXY_DEPLOYMENT


def test_hosted_proxy_mints_scoped_sandbox_credentials() -> None:
    assert "serviceAccountName: ufo-sandbox-proxy" in PROXY_DEPLOYMENT
    assert "eks.amazonaws.com/role-arn: ${proxy_role_arn}" in HOSTED_TEMPLATE.read_text()
    assert "name: UFO_SANDBOX_FS_TOKEN_SECRET" in PROXY_DEPLOYMENT
    assert "secretKeyRef: {name: ufo-serve, key: UFO_SANDBOX_FS_TOKEN_SECRET}" in PROXY_DEPLOYMENT


def test_hosted_proxy_and_serve_share_the_rendered_config() -> None:
    config_mount = "{name: config, mountPath: /app/ufo.toml, subPath: ufo.toml}"
    for deployment in (PROXY_DEPLOYMENT, SERVE_DEPLOYMENT):
        assert config_mount in deployment
        assert "secretName: ufo-serve" in deployment
        assert "{key: ufo.toml, path: ufo.toml}" in deployment
