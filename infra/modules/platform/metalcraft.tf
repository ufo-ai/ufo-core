# The metalcraft platform itself — our dogfooded chart. Terraform passes the
# environment's AWS wiring (image, IRSA, secret names, redis, hostname) as values.
# These values define the contract charts/metalcraft must implement (Deliverable 1).

# https://www.cloudflare.com/ips-v4 — the edge ranges Cloudflare connects to origins from. The NLB
# admits only these, so the gateway is unreachable except through Cloudflare's proxy (DDoS/WAF edge).
locals {
  cloudflare_ipv4_ranges = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
  ]
}

resource "helm_release" "metalcraft" {
  count = var.enable_app ? 1 : 0

  name             = "metalcraft"
  chart            = var.chart_path
  namespace        = local.system_namespace
  create_namespace = true
  timeout          = 600
  # Don't block on --wait: the sandbox-proxy Service is type LoadBalancer and, on a cluster whose LB
  # controller can't assign it an address, it never gets one, so --wait would hang until the token
  # expires. Workloads + the migrate Job reconcile asynchronously.
  wait = false

  values = [yamlencode({
    image = {
      registry = local.ecr_registry
      tag      = var.image_tag
    }

    gateway = {
      hostname       = var.hostname
      className      = "metalcraft"
      createClass    = true
      controllerName = "gateway.envoyproxy.io/gatewayclass-controller"
      internetFacing = true
      backend        = var.gateway_backend
      sourceRanges   = local.cloudflare_ipv4_ranges
    }

    sandbox = { backend = "e2b" }

    tenant = { indexBackend = var.index_backend }

    sandboxProxy = {
      hostname       = local.sandbox_proxy_hostname
      internetFacing = local.sandbox_egress_proxy_enabled
    }

    runtimeConfig = {
      e2bSandboxTemplate = var.e2b_sandbox_template
      sandboxEgressProxy = var.sandbox_egress_proxy
      sandboxProxyUrl    = local.sandbox_proxy_url
    }

    # OTLP → Datadog. environment is the Datadog `env` tag; the API key arrives via External Secrets
    # (api-keys SM secret, key datadog-api-key). datadog_enabled requires that key to be set first.
    observability = {
      enabled     = var.datadog_enabled
      environment = var.name
      datadog = {
        enabled = var.datadog_enabled
        site    = var.datadog_site
      }
    }

    redis = { url = local.redis_url }

    objectStore = {
      bucket           = aws_s3_bucket.store.id
      sandboxFsBucket  = aws_s3_bucket.sandbox_fs.id
      sandboxFsRoleArn = aws_iam_role.sandbox_fs.arn
      sandboxFsS3Url   = "https://s3.${var.region}.amazonaws.com"
      region           = var.region
    }

    # IRSA role annotation applied to the SAs that touch S3.
    serviceAccounts = {
      s3RoleArn         = module.irsa_app_s3.iam_role_arn
      s3ServiceAccounts = local.s3_service_accounts
    }

    externalSecrets = {
      enabled       = true
      region        = var.region
      secretStoreSA = "external-secrets"
      secrets = {
        postgres = aws_secretsmanager_secret.postgres.name
        platform = aws_secretsmanager_secret.platform.name
        apiKeys  = aws_secretsmanager_secret.api_keys.name
      }
    }

    certManager = {
      enabled     = true
      issuerEmail = var.letsencrypt_email
      acmeServer  = var.acme_server
    }
  })]

  depends_on = [
    helm_release.aws_load_balancer_controller,
    helm_release.cert_manager,
    helm_release.external_secrets,
    helm_release.external_dns,
    helm_release.envoy_gateway,
    helm_release.metrics_server,
    aws_secretsmanager_secret_version.postgres,
    aws_secretsmanager_secret_version.platform,
    aws_secretsmanager_secret_version.api_keys,
  ]
}
