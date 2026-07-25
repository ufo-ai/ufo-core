import re
from pathlib import Path

from ufo.serve import RESERVED_HOST_PREFIXES

HOSTED_TEMPLATE = Path(__file__).resolve().parents[2] / "infra/templates/hosted.yaml.tpl"
IAM_MODULE = Path(__file__).resolve().parents[2] / "infra/modules/platform/iam.tf"
CLUSTER_SERVICES_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "infra/templates/cluster-services.yaml.tpl"
)
PLATFORM_SECRETS = Path(__file__).resolve().parents[2] / "infra/modules/platform/secrets.tf"
APP_S3_MODULE = IAM_MODULE.read_text().split('module "irsa_app_s3" {', maxsplit=1)[1]


def test_app_s3_trusts_serve_in_every_ufo_namespace() -> None:
    assert 'assume_role_condition_test = "StringLike"' in APP_S3_MODULE
    assert 'namespace_service_accounts = ["ufo-*:ufo-serve"]' in APP_S3_MODULE


def test_hosted_serve_receives_the_bedrock_region() -> None:
    assert '- {name: AWS_REGION, value: "${region}"}' in HOSTED_TEMPLATE.read_text()


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
    proxy = (
        HOSTED_TEMPLATE.read_text()
        .split("name: ufo-sandbox-proxy", maxsplit=1)[1]
        .split("---", maxsplit=1)[0]
    )
    assert "name: COMPOSIO_API_KEY" in proxy
    assert "secretKeyRef: {name: ufo-platform-secrets, key: COMPOSIO_API_KEY}" in proxy
