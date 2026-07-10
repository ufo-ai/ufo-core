# The ufo application layer on top of the platform substrate (RFC 0011 §6): shared ingress
# (ingress-nginx), the cert-manager ClusterIssuer, the control plane (Tenant CRD + operator + API),
# its platform config, and the External Secrets that feed the operator and every tenant pod.

data "aws_caller_identity" "current" {}

locals {
  # https://www.cloudflare.com/ips-v4 — the edge ranges Cloudflare connects to origins from. The NLB
  # admits only these, so the ingress is unreachable except through Cloudflare's proxy (DDoS/WAF edge).
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

  system_namespace = module.platform.system_namespace

  # The digest-pinned bundle this deploy runs everywhere: the migrate Job and sandbox proxy run it
  # directly, and the operator advances every Tenant to it (platform.toml).
  bundle_image = "${module.platform.ecr_registry}/ufo@${data.aws_ecr_image.ufo.image_digest}"

  # PlatformConfig (ufo_control.platform) the operator mounts at /config/platform.toml. Names the
  # shared backing services + cluster facts the reconciler overlays onto every tenant's core config.
  platform_config = <<-TOML
    chart_path = "/charts/ufo-tenant"
    registry = "${module.platform.ecr_registry}"
    bundle_image = "${local.bundle_image}"

    tenant_postgres_host = "${module.platform.rds_endpoint}"
    redis_url = "redis://${module.platform.redis_endpoint}:6379/0"

    blob_bucket = "${module.platform.blob_bucket}"
    blob_region = "${var.region}"
    blob_s3_url = "https://s3.${var.region}.amazonaws.com"
    blob_sts_role_arn = "${module.platform.sandbox_fs_role_arn}"
    serve_role_arn = "${module.platform.app_s3_role_arn}"

    sandbox_proxy_url = "http://sandbox-proxy.${module.platform.hostname}:8888"

    ingress_class = "nginx"
    cluster_issuer = "letsencrypt"
    platform_secret = "ufo-platform-secrets"

    otlp_endpoint = "http://otel-collector.${local.system_namespace}.svc.cluster.local:4318"
  TOML

  # The migrate Job is split out of the shared for_each so its own resource can wait on
  # completion; everything else applies fire-and-forget.
  ufo_manifests = { for path, manifest in merge(
    data.kubectl_file_documents.tenant_crd.manifests,
    data.kubectl_file_documents.control_plane.manifests,
    data.kubectl_file_documents.cluster_services.manifests,
    data.kubectl_file_documents.observability.manifests,
  ) : path => manifest if !strcontains(path, "/jobs/ufo-migrate-") }

  ufo_migrate_manifest = one([
    for path, manifest in data.kubectl_file_documents.control_plane.manifests :
    manifest if strcontains(path, "/jobs/ufo-migrate-")
  ])

  # The one shared serve fleet's host: all hosted workspaces are served by this single fleet (no
  # per-workspace subdomain — RFC 0011), so one hostname fronts it, alongside the onboarding gateway
  # at the apex. Cloudflare-proxied like the tenant/gateway ingress.
  shared_host = "app.${module.platform.hostname}"

  # The shared fleet's ufo.toml, the hosted-tier production config: the assistant_hosted knobs the
  # gateway authors for a tenant (gateway_provision.CONFIG_TOML) plus the infra overlay the operator's
  # render.py applies per tenant (database/blob/hub/connect/sandbox/o11y) — here rendered once for the
  # ONE fleet. It connects as the RLS-subject ufo_serve role and resolves the workspace per request;
  # system_url is left to core's `<name>_dbos` derivation, so it resolves the shared `ufo_dbos` system
  # database. Carries the DSN password → the ufo-serve Secret, never a ConfigMap.
  serve_config = <<-TOML
    [pack]
    name = "assistant_hosted"

    [memory]
    index_backend = "turbopuffer"

    [research]
    search_provider = "exa"

    [browser]
    cdp_provider = "sandbox_chrome"

    [serve]
    host = "0.0.0.0"
    port = 8710
    shared_workspace = true

    [database]
    url = "${module.platform.serve_dsn}"

    [blob]
    backend = "s3"
    bucket = "${module.platform.blob_bucket}"
    region = "${var.region}"
    s3_url = "https://s3.${var.region}.amazonaws.com"
    sts_role_arn = "${module.platform.sandbox_fs_role_arn}"

    [hub]
    backend = "redis"
    url = "redis://${module.platform.redis_endpoint}:6379/0"

    [sandbox]
    backend = "e2b"
    proxy_public_url = "http://sandbox-proxy.${module.platform.hostname}:8888"

    [connect]
    public_base_url = "https://${local.shared_host}"

    [o11y]
    otlp_endpoint = "http://otel-collector.${local.system_namespace}.svc.cluster.local:4318"
  TOML
}

# The shared serve fleet's Secret (ufo-system): the rendered config (with the ufo_serve DSN, so a
# Secret) plus the platform Fernet / session / artifact keys the fleet reads from env. Mounted +
# referenced by the ufo-serve Deployment in control-plane.yaml.tpl. Not replicated to tenants.
resource "kubernetes_secret_v1" "ufo_serve" {
  metadata {
    name      = "ufo-serve"
    namespace = local.system_namespace
  }
  data = {
    "ufo.toml"                = local.serve_config
    UFO_CREDENTIAL_KEY        = module.platform.serve_credential_key
    UFO_SESSION_SECRET        = module.platform.serve_session_secret
    UFO_ARTIFACT_TOKEN_SECRET = module.platform.serve_artifact_token
  }
  depends_on = [kubernetes_namespace_v1.ufo_system]
}

# Shared ingress: the tenant chart's Ingress uses class "nginx" (RFC 0004 decision 6). Fronted by an
# internet-facing NLB the AWS LB controller provisions, source-restricted to Cloudflare's edge so the
# origin is reachable only through the proxy — the pattern the old Envoy gateway encoded.
resource "helm_release" "ingress_nginx" {
  name             = "ingress-nginx"
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
  version          = "4.11.3"
  namespace        = "ingress-nginx"
  create_namespace = true

  values = [yamlencode({
    controller = {
      ingressClassResource = { name = "nginx", default = false }
      service = {
        type                     = "LoadBalancer"
        loadBalancerSourceRanges = local.cloudflare_ipv4_ranges
        annotations = {
          "service.beta.kubernetes.io/aws-load-balancer-type"            = "external"
          "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type" = "ip"
          "service.beta.kubernetes.io/aws-load-balancer-scheme"          = "internet-facing"
        }
      }
    }
  })]

  depends_on = [module.platform]
}

# ufo-system: the control plane + apex workspace live here (the operator creates tenant namespaces).
resource "kubernetes_namespace_v1" "ufo_system" {
  metadata {
    name   = local.system_namespace
    labels = { "app.kubernetes.io/managed-by" = "ufo-control" }
  }
  depends_on = [module.platform]
}

resource "kubernetes_config_map_v1" "ufo_control_platform" {
  metadata {
    name      = "ufo-control-platform"
    namespace = local.system_namespace
  }
  data       = { "platform.toml" = local.platform_config }
  depends_on = [kubernetes_namespace_v1.ufo_system]
}

# The Tenant CRD is applied straight from the control-plane source of truth (no duplicate schema);
# the control plane + cluster services are templated with the prod image + secret names.
data "kubectl_file_documents" "tenant_crd" {
  content = file("${path.module}/../../../control/deploy/crds/tenant.yaml")
}

data "aws_ecr_image" "ufo" {
  repository_name = "ufo"
  image_tag       = var.image_tag
}

data "kubectl_file_documents" "control_plane" {
  content = templatefile("${path.module}/ufo/control-plane.yaml.tpl", {
    registry       = module.platform.ecr_registry
    image_tag      = var.image_tag
    namespace      = local.system_namespace
    base_domain    = module.platform.hostname
    shared_host    = local.shared_host
    cluster_issuer = "letsencrypt"
    ingress_class  = "nginx"
    bundle_image   = local.bundle_image
    serve_role_arn = module.platform.app_s3_role_arn
    otlp_endpoint  = "http://otel-collector.${local.system_namespace}.svc.cluster.local:4318"
  })
}

data "kubectl_file_documents" "cluster_services" {
  content = templatefile("${path.module}/ufo/cluster-services.yaml.tpl", {
    acme_server     = var.acme_server
    acme_email      = var.letsencrypt_email
    dns_zone        = module.platform.hostname
    region          = var.region
    namespace       = local.system_namespace
    secret_postgres = module.platform.secret_names.postgres
    secret_platform = module.platform.secret_names.platform
    secret_api_keys = module.platform.secret_names.api_keys
  })
}

# Shared OpenTelemetry collector (ufo-system): tenant serve pods export OTLP to it and it forwards to
# Datadog. Pinned public collector-contrib image (carries the datadog exporter); DD key from the
# datadog-api-key Secret (api-keys SM entry). The platform_config points tenants at its OTLP/HTTP port.
data "kubectl_file_documents" "observability" {
  content = templatefile("${path.module}/ufo/observability.yaml.tpl", {
    namespace = local.system_namespace
    # 0.114.0 pinned by its amd64 digest: the :0.115.0 tag does not exist and the :0.116.0 build's
    # binary fails to exec on the nodes; this digest is verified running the datadog exporter.
    collector_image = "otel/opentelemetry-collector-contrib@sha256:94ac10da6c15fdad4f8091c4292a8c6814b467cd3bcf575ba2279e9dc6346e63"
    dd_site         = "us5.datadoghq.com"
    dd_env          = "prod"
    secret_api_keys = module.platform.secret_names.api_keys
  })
}

resource "kubectl_manifest" "ufo" {
  for_each  = local.ufo_manifests
  yaml_body = each.value

  depends_on = [
    module.platform,
    kubernetes_namespace_v1.ufo_system,
    kubernetes_config_map_v1.ufo_control_platform,
    kubernetes_secret_v1.ufo_serve,
  ]
}

# The deploy is only done when the schema migration + RLS bootstrap have actually run: waiting on
# the Job's success makes a failed bootstrap fail the apply, instead of shipping green while the
# Job crash-loops unseen.
resource "kubectl_manifest" "ufo_migrate" {
  yaml_body = local.ufo_migrate_manifest

  wait_for {
    field {
      key   = "status.succeeded"
      value = "1"
    }
  }

  timeouts {
    create = "10m"
  }

  depends_on = [
    module.platform,
    kubernetes_namespace_v1.ufo_system,
    kubernetes_config_map_v1.ufo_control_platform,
    kubernetes_secret_v1.ufo_serve,
  ]
}
