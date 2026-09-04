import re
from pathlib import Path

import yaml

from infra.production_secrets import API_KEYS_PROPERTIES
from ufo.serve import RESERVED_HOST_PREFIXES

HOSTED_TEMPLATE = Path(__file__).resolve().parents[2] / "infra/templates/hosted.yaml.tpl"
IAM_MODULE = Path(__file__).resolve().parents[2] / "infra/modules/platform/iam.tf"
CLUSTER_SERVICES_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "infra/templates/cluster-services.yaml.tpl"
)
PLATFORM_SECRETS = Path(__file__).resolve().parents[2] / "infra/modules/platform/secrets.tf"
PLATFORM_EKS = Path(__file__).resolve().parents[2] / "infra/modules/platform/eks.tf"
PLATFORM_VARIABLES = Path(__file__).resolve().parents[2] / "infra/modules/platform/variables.tf"
PLATFORM_S3 = Path(__file__).resolve().parents[2] / "infra/modules/platform/s3.tf"
TESTING_MAIN = Path(__file__).resolve().parents[2] / "infra/envs/testing/main.tf"
PROD_MAIN = Path(__file__).resolve().parents[2] / "infra/envs/prod/main.tf"


def _terraform_block(header: str) -> str:
    """One top-level block of the IAM module. Bounded at its closing brace, so an assertion about
    one role cannot be satisfied by the next role's text."""
    return IAM_MODULE.read_text().split(header, maxsplit=1)[1].split("\n}\n", maxsplit=1)[0]


APP_S3_MODULE = _terraform_block('module "irsa_app_s3" {')
PROXY_DEPLOYMENT = (
    HOSTED_TEMPLATE.read_text()
    .split("kind: Deployment\nmetadata:\n  name: ufo-sandbox-proxy", maxsplit=1)[1]
    .split("---", maxsplit=1)[0]
)
INGRESS_DEPLOYMENT = (
    HOSTED_TEMPLATE.read_text()
    .split("kind: Deployment\nmetadata:\n  name: ufo-ingress", maxsplit=1)[1]
    .split("---", maxsplit=1)[0]
)
SERVE_DEPLOYMENT = (
    HOSTED_TEMPLATE.read_text()
    .split("kind: Deployment\nmetadata:\n  name: ufo-serve", maxsplit=1)[1]
    .split("---", maxsplit=1)[0]
)
TESTING_CONFIG = Path(__file__).resolve().parents[2] / "infra/envs/testing/ufo.tf"
PROD_CONFIG = Path(__file__).resolve().parents[2] / "infra/envs/prod/ufo.tf"
PRODUCTION_WORKLOADS = ("ufo-gateway", "ufo-sandbox-proxy", "ufo-ingress", "ufo-serve")


def _documents(workload_ha: bool) -> list[dict[str, object]]:
    rendered = re.sub(
        r"%\{ if workload_ha \}\n(.*?)%\{ endif \}\n",
        lambda match: match.group(1) if workload_ha else "",
        HOSTED_TEMPLATE.read_text(),
        flags=re.DOTALL,
    )
    rendered = re.sub(
        r"(?m)^%\{ [^}]*\}\n?",
        "",
        rendered,
    )
    rendered = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", "value", rendered)
    return list(yaml.safe_load_all(rendered))


def _document(documents: list[dict[str, object]], kind: str, name: str) -> dict[str, object]:
    return next(
        document
        for document in documents
        if document["kind"] == kind and document["metadata"]["name"] == name
    )


def _check_app_s3_trusts_serve_in_every_ufo_namespace() -> None:
    assert 'assume_role_condition_test = "StringLike"' in APP_S3_MODULE
    assert 'namespace_service_accounts = ["ufo-*:ufo-serve"]' in APP_S3_MODULE
    assert "ufo-ingress" not in APP_S3_MODULE


def _check_app_s3_role_name_is_plan_known_and_shared() -> None:
    platform = Path(__file__).resolve().parents[2] / "infra/modules/platform"
    assert re.search(
        r'app_s3_role_name\s+= "\$\{local\.name\}-app-s3"', (platform / "main.tf").read_text()
    )
    iam = IAM_MODULE.read_text()
    assert "name   = local.app_s3_role_name" in iam
    assert "role_name        = local.app_s3_role_name" in iam
    assert (
        'value       = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:'
        'role/${local.app_s3_role_name}"' in (platform / "outputs.tf").read_text()
    )


def _check_ingress_reads_the_blob_bucket_under_a_read_only_role() -> None:
    """The ingress streams a stored site's bytes out of the blob store, so it needs a credential for
    that bucket — and it is the internet-facing reverse proxy, so it gets the narrowest one that
    serves a byte: GetObject plus the bucket-level reads, never PutObject or DeleteObject. Sharing
    ufo-serve's ServiceAccount would hand this pod the whole read-write grant that promotes a
    deploy, so the split is pinned at both ends — its own SA, its own IRSA role, its own policy."""
    platform = Path(__file__).resolve().parents[2] / "infra/modules/platform"
    assert re.search(
        r'ingress_s3_role_name\s+= "\$\{local\.name\}-ingress-s3"',
        (platform / "main.tf").read_text(),
    )
    assert (
        'value       = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:'
        'role/${local.ingress_s3_role_name}"' in (platform / "outputs.tf").read_text()
    )
    for config in (TESTING_CONFIG, PROD_CONFIG):
        assert (
            "ingress_role_arn                 = module.platform.ingress_s3_role_arn"
            in config.read_text()
        )

    role = _terraform_block('module "irsa_ingress_s3" {')
    assert "role_name        = local.ingress_s3_role_name" in role
    assert 'assume_role_condition_test = "StringLike"' in role
    assert 'namespace_service_accounts = ["ufo-*:ufo-ingress"]' in role

    policy = _terraform_block('data "aws_iam_policy_document" "ingress_s3" {')
    assert 'actions   = ["s3:GetObject"]' in policy
    assert 'resources = ["${aws_s3_bucket.blob.arn}/*"]' in policy
    assert 'actions   = ["s3:ListBucket", "s3:GetBucketLocation"]' in policy
    assert "s3:PutObject" not in policy
    assert "s3:DeleteObject" not in policy

    account = _document(_documents(False), "ServiceAccount", "ufo-ingress")
    assert account["metadata"]["annotations"] == {"eks.amazonaws.com/role-arn": "value"}
    assert "eks.amazonaws.com/role-arn: ${ingress_role_arn}" in HOSTED_TEMPLATE.read_text()
    assert "serviceAccountName: ufo-ingress" in INGRESS_DEPLOYMENT
    assert "ufo-serve" not in INGRESS_DEPLOYMENT.split("containers:", maxsplit=1)[0]


def _check_cache_outputs_are_plan_known() -> None:
    """The cache bucket name and role ARN render into the hosted manifest, whose keys feed a
    for_each that must be known at plan time. So the outputs are built from plan-known inputs, never
    from an apply-time attribute like `aws_s3_bucket.cache.id` that would break the plan on a fresh
    bucket."""
    outputs = (
        Path(__file__).resolve().parents[2] / "infra/modules/platform/outputs.tf"
    ).read_text()

    def value_line(name: str) -> str:
        block = outputs.split(f'output "{name}"', maxsplit=1)[1].split("output", 1)[0]
        return next(line for line in block.splitlines() if line.strip().startswith("value"))

    bucket = value_line("cache_s3_bucket")
    assert "aws_s3_bucket.cache.id" not in bucket
    assert "${local.name}-ufo-cache-${data.aws_caller_identity.current.account_id}" in bucket
    assert "role/${local.cache_s3_role_name}" in value_line("cache_s3_role_arn")


def _check_platform_grants_the_proxy_only_cache_scoped_s3() -> None:
    """The sandbox itself reaches nothing under a cloud role — its workspace lives on the carrier's
    filesystem and artifact PUTs are presigned serve-side, so there is no sandbox-fs role and no
    hand-rolled AssumeRole policy. The proxy pod's one cloud grant is the cache sidecar's durable
    tier: an IRSA role bound to ufo-sandbox-proxy, scoped to the cache bucket alone — never the blob
    bucket serve reaches."""
    module = IAM_MODULE.read_text()
    assert "sandbox_fs" not in module
    assert not re.search(r"sts:AssumeRole\b", module)
    cache_role = module.split('module "irsa_cache_s3" {', maxsplit=1)[1]
    assert 'namespace_service_accounts = ["ufo-*:ufo-sandbox-proxy"]' in cache_role
    cache_policy = module.split('data "aws_iam_policy_document" "cache_s3" {', maxsplit=1)[1].split(
        'resource "aws_iam_policy" "cache_s3"', maxsplit=1
    )[0]
    assert "aws_s3_bucket.cache.arn" in cache_policy
    assert "aws_s3_bucket.blob" not in cache_policy


def _check_hosted_serve_receives_the_bedrock_region() -> None:
    assert '- {name: AWS_REGION, value: "${region}"}' in HOSTED_TEMPLATE.read_text()


def _check_production_ingress_survives_a_node_or_zone_loss() -> None:
    _, start, remainder = PROD_CONFIG.read_text().partition(
        'resource "helm_release" "ingress_nginx" {'
    )
    assert start
    production, end, _ = remainder.partition("\n}\n")
    assert end
    assert re.search(r"^\s+replicaCount\s+= 2$", production, re.MULTILINE)
    assert re.search(r"^\s+minAvailable\s+= 1$", production, re.MULTILINE)
    assert re.search(r"^\s+timeout\s+= 900$", production, re.MULTILINE)
    assert "requiredDuringSchedulingIgnoredDuringExecution" in production
    assert "preferredDuringSchedulingIgnoredDuringExecution" not in production
    for label in (
        '"app.kubernetes.io/component" = "controller"',
        '"app.kubernetes.io/instance"  = "ingress-nginx"',
        '"app.kubernetes.io/name"      = "ingress-nginx"',
    ):
        assert production.count(label) == 2
    assert 'topologyKey = "kubernetes.io/hostname"' in production
    assert re.search(r"^\s+maxSkew\s+= 1$", production, re.MULTILINE)
    assert 'topologyKey       = "topology.kubernetes.io/zone"' in production
    assert 'whenUnsatisfiable = "DoNotSchedule"' in production
    assert (
        '"service.beta.kubernetes.io/aws-load-balancer-attributes"      = '
        '"load_balancing.cross_zone.enabled=true"' in production
    )


def _check_production_workloads_survive_a_node_or_zone_loss() -> None:
    assert re.search(r"^\s+workload_ha\s+= true$", PROD_CONFIG.read_text(), re.MULTILINE)
    documents = _documents(True)
    for name in PRODUCTION_WORKLOADS:
        deployment = _document(documents, "Deployment", name)
        pod = deployment["spec"]["template"]["spec"]
        assert pod["affinity"]["podAntiAffinity"] == {
            "preferredDuringSchedulingIgnoredDuringExecution": [
                {
                    "podAffinityTerm": {
                        "labelSelector": {"matchLabels": {"app": name}},
                        "topologyKey": "kubernetes.io/hostname",
                    },
                    "weight": 100,
                }
            ]
        }
        assert pod["topologySpreadConstraints"] == [
            {
                "labelSelector": {"matchLabels": {"app": name}},
                "maxSkew": 1,
                "matchLabelKeys": ["pod-template-hash"],
                "nodeTaintsPolicy": "Honor",
                "topologyKey": "topology.kubernetes.io/zone",
                "whenUnsatisfiable": "DoNotSchedule",
            }
        ]
        budget = _document(documents, "PodDisruptionBudget", name)
        assert budget["spec"] == {
            "minAvailable": 1,
            "selector": {"matchLabels": {"app": name}},
            "unhealthyPodEvictionPolicy": "AlwaysAllow",
        }


def _check_testing_workloads_keep_the_current_placement() -> None:
    assert re.search(r"^\s+workload_ha\s+= false$", TESTING_CONFIG.read_text(), re.MULTILINE)
    documents = _documents(False)
    assert not [document for document in documents if document["kind"] == "PodDisruptionBudget"]
    for name in PRODUCTION_WORKLOADS:
        deployment = _document(documents, "Deployment", name)
        pod = deployment["spec"]["template"]["spec"]
        assert "affinity" not in pod
        assert "topologySpreadConstraints" not in pod


def _check_hosted_serve_rolls_all_replacements_before_draining() -> None:
    assert "maxSurge: 100%" in SERVE_DEPLOYMENT
    assert "maxUnavailable: 0" in SERVE_DEPLOYMENT
    assert "terminationGracePeriodSeconds: ${termination_grace_period_seconds}" in SERVE_DEPLOYMENT
    assert 'command: [sleep, "${prestop_seconds}"]' in SERVE_DEPLOYMENT


def _check_hosted_workloads_reserve_capacity() -> None:
    """A container with no request is BestEffort: the first the kubelet evicts under node pressure,
    and the smallest CPU share under contention. Serve alone carries no memory limit — a turn's
    model rounds ride its process, so a limit there trades a slow pod for a killed turn."""
    documents = _documents(False)
    for name in PRODUCTION_WORKLOADS:
        deployment = _document(documents, "Deployment", name)
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            requests = container["resources"]["requests"]
            assert requests["cpu"] and requests["memory"], (name, container["name"])
    serve = _document(documents, "Deployment", "ufo-serve")
    assert serve["spec"]["template"]["spec"]["containers"][0]["resources"] == {
        "requests": {"cpu": "250m", "memory": "512Mi"},
    }


def _check_hosted_proxy_rolls_all_replacements_and_drains_connections() -> None:
    assert "maxSurge: 100%" in PROXY_DEPLOYMENT
    assert "maxUnavailable: 0" in PROXY_DEPLOYMENT
    assert "terminationGracePeriodSeconds: ${termination_grace_period_seconds}" in PROXY_DEPLOYMENT
    assert 'command: [sleep, "${prestop_seconds}"]' in PROXY_DEPLOYMENT


def _check_hosted_shutdown_grace_matches_across_environments() -> None:
    """The env local is the single source for each drain window: the rendered `[serve]` config
    interpolates the local (never a second literal), and the pod's termination grace is computed
    from both sequential shutdown phases — so no value can drift from its enforcement. Both envs
    hold a rolling pod open for the in-flight turn, which is what carries the model round."""
    testing = TESTING_CONFIG.read_text()
    prod = PROD_CONFIG.read_text()
    for config in (testing, prod):
        assert "  graceful_shutdown_seconds = 600\n" in config
        assert "  prestop_seconds           = 10\n" in config
        assert "  request_shutdown_seconds  = 30\n" in config
        assert "prestop_seconds                  = local.prestop_seconds" in config
        assert "request_shutdown_seconds = ${local.request_shutdown_seconds}" in config
        assert "graceful_shutdown_seconds = ${local.graceful_shutdown_seconds}" in config
        assert (
            "termination_grace_period_seconds = local.prestop_seconds + "
            "local.request_shutdown_seconds + local.graceful_shutdown_seconds + 60"
        ) in config


def _check_hosted_serve_receives_the_bedrock_mantle_api_key() -> None:
    assert "bedrock-api-key" in API_KEYS_PROPERTIES
    assert (
        "{secretKey: AWS_BEARER_TOKEN_BEDROCK, "
        "remoteRef: {key: ${secret_api_keys}, property: bedrock-api-key}}"
        in CLUSTER_SERVICES_TEMPLATE.read_text()
    )


def _check_hosted_serve_receives_the_perplexity_api_key() -> None:
    assert (
        "{secretKey: PERPLEXITY_API_KEY, "
        "remoteRef: {key: ${secret_api_keys}, property: perplexity-api-key}}"
        in CLUSTER_SERVICES_TEMPLATE.read_text()
    )


def _check_hosted_serve_receives_the_spectrum_project_credentials() -> None:
    projections = CLUSTER_SERVICES_TEMPLATE.read_text()
    for name, property_name in (
        ("SPECTRUM_PROJECT_ID", "spectrum-project-id"),
        ("SPECTRUM_PROJECT_SECRET", "spectrum-project-secret"),
    ):
        assert property_name in API_KEYS_PROPERTIES
        assert (
            f"{{secretKey: {name}, remoteRef: "
            f"{{key: ${{secret_api_keys}}, property: {property_name}}}}}" in projections
        )


def _check_app_host_ingress_routes_login_to_gateway_and_product_to_serve() -> None:
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


def _check_app_host_ingress_outlives_the_terminal_surface_hold() -> None:
    blocks = HOSTED_TEMPLATE.read_text().split("kind: Ingress")
    serve_ingress = next(b for b in blocks if "name: ufo-serve" in b.split("---", 1)[0])
    configured = re.search(
        r'nginx\.ingress\.kubernetes\.io/proxy-read-timeout: "(\d+)"', serve_ingress
    )
    surface = (
        Path(__file__).resolve().parents[2] / "extensions/ufo/ufo_ext_ufo/surface.py"
    ).read_text()
    held = re.search(r"^HOLD_SECONDS = ([\d.]+)$", surface, re.MULTILINE)

    assert configured and held
    assert int(configured.group(1)) > float(held.group(1))


def _check_hosted_proxy_runs_the_ufo_egress_data_plane() -> None:
    """The proxy pod runs the standalone ufo-egress image as its entrypoint (no `args`), binds 8888,
    and reaches serve's egress-control RPC: the control URL on serve's internal port, the bearer and
    the run-token secret from ufo-platform-secrets, and the signing CA (cert + key) from
    ufo-egress-ca. With the cache on it relays to the loopback cache daemon."""
    assert "image: ${registry}/ufo-egress:${image_tag}" in PROXY_DEPLOYMENT
    assert "args:" not in PROXY_DEPLOYMENT
    assert '- {name: UFO_EGRESS_PORT, value: "8888"}' in PROXY_DEPLOYMENT
    assert (
        "- {name: UFO_EGRESS_CONTROL_URL, "
        'value: "http://ufo-serve.${namespace}.svc.cluster.local:8710"}' in PROXY_DEPLOYMENT
    )
    assert "name: UFO_EGRESS_CONTROL_TOKEN" in PROXY_DEPLOYMENT
    assert (
        "secretKeyRef: {name: ufo-platform-secrets, key: UFO_EGRESS_CONTROL_TOKEN}"
        in PROXY_DEPLOYMENT
    )
    for ca_key in ("UFO_EGRESS_CA_CERT", "UFO_EGRESS_CA_KEY"):
        assert f"secretKeyRef: {{name: ufo-egress-ca, key: {ca_key}}}" in PROXY_DEPLOYMENT
    assert '- {name: UFO_EGRESS_CACHE_DAEMON, value: "127.0.0.1:9110"}' in PROXY_DEPLOYMENT


def _check_hosted_proxy_is_a_keyless_data_plane() -> None:
    """The ufo-egress data plane holds no provider, broker, or credential keys and no database — it
    verifies the run token locally and calls serve's egress-control RPC for every decision. So every
    secret the deleted Python proxy carried (the model/provider keys, the broker keys, the
    credential Fernet, the RLS-bypassing owner DSN) is gone from its pod; only the token secret, the
    control bearer, and the signing CA remain."""
    for absent in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "COMPOSIO_API_KEY",
        "PIPEDREAM_CLIENT_SECRET",
        "PIPEDREAM_GITHUB_OAUTH_APP_ID",
        "UFO_CREDENTIAL_KEY",
        "UFO_OWNER_DSN",
    ):
        assert absent not in PROXY_DEPLOYMENT


def _check_egress_control_token_is_minted_and_projected() -> None:
    """The bearer the ufo-egress data plane presents to serve's egress-control RPC is minted in
    terraform, written into the platform secret, and projected into ufo-platform-secrets — so serve
    (which reads that secret whole via envFrom) and the proxy (an explicit secretKeyRef) resolve one
    shared value."""
    secrets_tf = PLATFORM_SECRETS.read_text()
    assert 'resource "random_password" "egress_control_token"' in secrets_tf
    assert '"egress-control-token"' in secrets_tf
    assert "random_password.egress_control_token.result" in secrets_tf
    assert (
        "{secretKey: UFO_EGRESS_CONTROL_TOKEN, "
        "remoteRef: {key: ${secret_platform}, property: egress-control-token}}"
        in CLUSTER_SERVICES_TEMPLATE.read_text()
    )


def _check_hosted_pipedream_github_oauth_app_is_projected_for_serve() -> None:
    """GitHub rides the `github` connector's grant, whose consent runs on the deploy's own OAuth
    client registered with Pipedream, so the client's app id is projected into ufo-platform-secrets,
    which serve reads whole via envFrom. No GitHub App registration is projected anywhere."""
    platform_secrets = CLUSTER_SERVICES_TEMPLATE.read_text()
    assert (
        "{secretKey: PIPEDREAM_GITHUB_OAUTH_APP_ID, remoteRef: "
        "{key: ${secret_api_keys}, property: pipedream-github-oauth-app-id}}" in platform_secrets
    )
    assert "pipedream-github-oauth-app-id" in API_KEYS_PROPERTIES
    assert "GITHUB_APP_" not in platform_secrets
    assert "GITHUB_APP_" not in HOSTED_TEMPLATE.read_text()


def _check_hosted_serve_holds_the_fleet_credential_key() -> None:
    """A keyed provider's secret is decrypted serve-side now — the egress data plane holds no keys —
    so serve, not the proxy, opens the fleet Fernet."""
    assert "name: UFO_CREDENTIAL_KEY" in SERVE_DEPLOYMENT
    assert "secretKeyRef: {name: ufo-serve, key: UFO_CREDENTIAL_KEY}" in SERVE_DEPLOYMENT


def _check_hosted_proxy_receives_the_run_token_signing_secret() -> None:
    assert "name: UFO_TOKEN_SECRET" in PROXY_DEPLOYMENT
    assert "secretKeyRef: {name: ufo-platform-secrets, key: UFO_TOKEN_SECRET}" in PROXY_DEPLOYMENT


def _check_hosted_proxy_has_resource_bounds() -> None:
    assert "requests: {cpu: 250m, memory: 384Mi}" in PROXY_DEPLOYMENT
    assert 'limits: {cpu: "2", memory: 768Mi}' in PROXY_DEPLOYMENT


def _check_hosted_cache_reserves_node_storage() -> None:
    disk_size = re.search(r"node_disk_size\s+=\s+(\d+)", TESTING_MAIN.read_text())
    cache_size = re.search(r"emptyDir: \{sizeLimit: (\d+)Gi\}", PROXY_DEPLOYMENT)

    assert disk_size and cache_size
    assert int(disk_size.group(1)) >= 2 * int(cache_size.group(1))
    assert "volume_size           = var.node_disk_size" in PLATFORM_EKS.read_text()
    assert "default     = null" in PLATFORM_VARIABLES.read_text()
    assert "requests: {cpu: 250m, memory: 512Mi, ephemeral-storage: 32Gi}" in PROXY_DEPLOYMENT
    assert 'limits: {cpu: "2", memory: 3Gi, ephemeral-storage: 32Gi}' in PROXY_DEPLOYMENT


def _check_hosted_proxy_carries_only_the_cache_scoped_identity() -> None:
    """The proxy pod holds no broad cloud role: artifact PUTs are presigned serve-side, so it needs
    no blob access and there is no `proxy_role_arn` input. Its one cloud grant is the cache
    sidecar's — the cache-bucket-scoped IRSA role, annotated on the SA only when the cache is
    enabled, so a disk-only or cache-off deploy carries no identity at all."""
    template = HOSTED_TEMPLATE.read_text()
    assert "serviceAccountName: ufo-sandbox-proxy" in PROXY_DEPLOYMENT
    assert "proxy_role_arn" not in template
    proxy_account = template.split(
        "kind: ServiceAccount\nmetadata:\n  name: ufo-sandbox-proxy", maxsplit=1
    )[1].split("---", maxsplit=1)[0]
    assert "app_s3_role_arn" not in proxy_account
    assert "%{ if cache_enabled }" in proxy_account
    assert "eks.amazonaws.com/role-arn: ${cache_s3_role_arn}" in proxy_account


def _check_sites_answer_one_label_under_the_apex_behind_the_proxy() -> None:
    """Three deploy facts decide a site's address, and all are unforgiving.

    The NLB admits only `cloudflare_ipv4_ranges`, so a site address that resolves straight to it is
    dropped on every port — proxying is the only way a site is reachable at all. A wildcard TLS SAN
    matches exactly one label, so the zone's edge certificate (`*.<apex>`) covers a site one label
    under the apex and covers nothing beneath a `sites.` prefix. And the portal frames each site,
    whose view cookie is `SameSite=Lax` — so the site and the portal host must share a registrable
    domain, which deriving both from the apex guarantees. Pinning the depth, the proxy annotation,
    and the source-range restriction together keeps the three from drifting into a combination
    that publishes site addresses no browser can open."""
    sites_ingress = next(
        block
        for block in HOSTED_TEMPLATE.read_text().split("kind: Ingress")
        if "name: ufo-ingress" in block.split("---", maxsplit=1)[0]
    ).split("---", maxsplit=1)[0]
    assert 'external-dns.alpha.kubernetes.io/hostname: "*.${apex_host}"' in sites_ingress
    assert 'external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"' in sites_ingress
    assert '- hosts: ["*.${apex_host}"]' in sites_ingress
    assert '- host: "*.${apex_host}"' in sites_ingress
    assert "sites." not in sites_ingress.replace("`sites.` prefix", "")
    for env, apex in ((TESTING_CONFIG, "var.apex_host"), (PROD_CONFIG, "local.apex_host")):
        config = env.read_text()
        assert '    ingress_public_url = "https://${module.platform.hostname}"' in config
        assert "loadBalancerSourceRanges = local.cloudflare_ipv4_ranges" in config
        assert f'  shared_host         = "app.${{{apex}}}"' in config


def _check_the_blob_bucket_admits_a_presigned_put_from_the_portal_and_nothing_else() -> None:
    """A web attachment travels as one presigned PUT the member's browser sends straight to the
    bucket, from the portal page and carrying the checksum header the mint signed — a header no
    browser sends without asking S3 first. A bucket with no CORS rule refuses that ask, and every
    attachment falls back to the composer body the presigned path exists to keep the bytes out of.
    The rule stays as narrow as the URL it serves: PUT alone, from the portal origins each
    environment names, never a wildcard and never a read."""
    rule = (
        PLATFORM_S3.read_text()
        .split('resource "aws_s3_bucket_cors_configuration" "blob"', maxsplit=1)[1]
        .split("\n}\n", maxsplit=1)[0]
    )
    assert 'allowed_methods = ["PUT"]' in rule
    assert "allowed_origins = var.blob_origins" in rule
    assert "x-amz-checksum-sha256" in rule
    assert '"*"' not in rule
    for main in (TESTING_MAIN, PROD_MAIN):
        assert 'blob_origins         = ["https://${local.shared_host}"]' in main.read_text()


def _check_serve_mounts_the_rendered_config_and_the_proxy_reads_env() -> None:
    """serve mounts the rendered shared-fleet ufo.toml; the ufo-egress data plane reads its whole
    configuration from env and mounts no config, so only serve carries the config volume."""
    config_mount = "{name: config, mountPath: /app/ufo.toml, subPath: ufo.toml}"
    assert config_mount in SERVE_DEPLOYMENT
    assert "secretName: ufo-serve" in SERVE_DEPLOYMENT
    assert "{key: ufo.toml, path: ufo.toml}" in SERVE_DEPLOYMENT
    assert config_mount not in PROXY_DEPLOYMENT
    assert "ufo.toml" not in PROXY_DEPLOYMENT


def _rendered_cluster_services() -> list[dict[str, object]]:
    rendered = re.sub(
        r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", "value", CLUSTER_SERVICES_TEMPLATE.read_text()
    )
    return [document for document in yaml.safe_load_all(rendered) if document]


def _check_every_secret_key_a_workload_reads_is_one_an_external_secret_supplies() -> None:
    """A `secretKeyRef` naming a key no ExternalSecret syncs is not a plan error, a terraform error,
    or a test failure — the pod stops at `CreateContainerConfigError` and the rollout times out. The
    `data` lists are enumerated key by key, so writing a value into the AWS secret is only half of
    wiring it, and nothing else compares the two halves.
    """
    supplied: dict[str, set[str]] = {}
    for document in _rendered_cluster_services():
        if document.get("kind") != "ExternalSecret":
            continue
        target = document["spec"].get("target", {}).get("name") or document["metadata"]["name"]
        keys = {entry["secretKey"] for entry in document["spec"].get("data", [])}
        supplied.setdefault(target, set()).update(keys)
        # A `dataFrom` block syncs the whole secret, so anything it targets is unconstrained here.
        if document["spec"].get("dataFrom"):
            supplied[target].add("*")

    read: set[tuple[str, str]] = set()
    for workload_ha in (False, True):
        for document in _documents(workload_ha):
            if not document or document.get("kind") not in {"Deployment", "Job", "CronJob"}:
                continue
            for reference in re.finditer(
                r"secretKeyRef:\s*\{name:\s*([\w-]+),\s*key:\s*([\w.-]+)\}",
                yaml.safe_dump(document),
            ):
                read.add((reference.group(1), reference.group(2)))

    # `safe_dump` reflows the flow-style mappings, so read the references off the source instead.
    read = {
        (match.group(1), match.group(2))
        for match in re.finditer(
            r"secretKeyRef:\s*\{name:\s*([\w-]+),\s*key:\s*([\w.-]+)\}",
            HOSTED_TEMPLATE.read_text(),
        )
    }
    assert read, "no workload reads a secret key — the template parse is wrong, not the manifests"

    missing = sorted(
        f"{secret}.{key}"
        for secret, key in read
        if secret in supplied and "*" not in supplied[secret] and key not in supplied[secret]
    )
    assert not missing, (
        f"secretKeyRef names keys no ExternalSecret syncs: {missing}. "
        f"Add each to the matching `data` list in {CLUSTER_SERVICES_TEMPLATE.name}."
    )


def test_hosted_irsa_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 31
    for check in checks:
        check()
