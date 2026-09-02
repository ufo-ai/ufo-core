# The shared hosted application: gateway, serve fleet, sandbox proxy, and observability.

data "aws_caller_identity" "current" {}

locals {
  # The sandbox cache (RFC 0032): git mirrors + package registries, on for testing. The image builds
  # every deploy and the UFO_CACHE_CONTROL_TOKEN secret is provisioned, so the sidecar has both when
  # it rolls. `cache_s3_bucket` stays empty — disk-only, cold on each pod roll. Prod stays off.
  cache_enabled = true

  # The preview service (RFC 0037), on for testing: the image builds every deploy and the
  # UFO_PREVIEW_TOKEN secret is provisioned, so the Deployment has both when it rolls. Prod stays off
  # until proven here.
  preview_enabled = true

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

  # The three sequential shutdown phases. `graceful_shutdown_seconds` is the window `DBOS.destroy`
  # polls in-flight turns for before it cancels them, so the model round a roll interrupts finishes
  # and records. Matches prod: the pods' SIGKILL deadline derives from these below, so a drain is
  # bounded at 700s.
  prestop_seconds           = 10
  request_shutdown_seconds  = 30
  graceful_shutdown_seconds = 600

  # The Datadog organization the fleet reports to, and the tag that separates this deploy's
  # telemetry from production's — worn by the collector's exports and by the portal's recordings.
  datadog_site = "us5.datadoghq.com"
  datadog_env  = "testing"

  # The digest-pinned runtime image used by migration, proxy, and serve.
  bundle_image = "${module.platform.ecr_registry}/ufo@${data.aws_ecr_image.ufo.image_digest}"

  # The terminal client version this deploy serves and update-gates on, read from the crate the
  # deploy's binaries were built from — one source for the images and both pods' env.
  client_version = regex("(?m)^version = \"([^\"]+)\"", file("${path.module}/../../../client/Cargo.toml"))[0]

  ufo_prerequisite_manifests = data.kubectl_file_documents.cluster_services.manifests

  sandbox_proxy_manifests = {
    "/api/v1/namespaces/${local.system_namespace}/services/ufo-sandbox-proxy" = yamlencode({
      apiVersion = "v1"
      kind       = "Service"
      metadata = {
        name      = "ufo-sandbox-proxy"
        namespace = local.system_namespace
        labels    = { app = "ufo-sandbox-proxy" }
        annotations = {
          "external-dns.alpha.kubernetes.io/hostname"                           = "sandbox-proxy.${module.platform.hostname}"
          "external-dns.alpha.kubernetes.io/cloudflare-proxied"                 = "false"
          "service.beta.kubernetes.io/aws-load-balancer-attributes"             = "load_balancing.cross_zone.enabled=true"
          "service.beta.kubernetes.io/aws-load-balancer-type"                   = "external"
          "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type"        = "ip"
          "service.beta.kubernetes.io/aws-load-balancer-scheme"                 = "internet-facing"
          "service.beta.kubernetes.io/aws-load-balancer-ssl-cert"               = module.platform.sandbox_proxy_certificate_arn
          "service.beta.kubernetes.io/aws-load-balancer-ssl-ports"              = "443"
          "service.beta.kubernetes.io/aws-load-balancer-ssl-negotiation-policy" = "ELBSecurityPolicy-TLS13-1-2-2021-06"
        }
      }
      spec = {
        selector = { app = "ufo-sandbox-proxy" }
        type     = "LoadBalancer"
        ports = [{
          name       = "proxy-tls"
          port       = 443
          targetPort = "proxy"
          protocol   = "TCP"
        }]
      }
    })
  }

  ufo_workload_manifests = { for path, manifest in merge(
    data.kubectl_file_documents.hosted.manifests,
    data.kubectl_file_documents.observability.manifests,
    local.sandbox_proxy_manifests,
  ) : path => manifest if !strcontains(path, "/jobs/ufo-migrate-") }

  ufo_migrate_manifest = one([
    for path, manifest in data.kubectl_file_documents.hosted.manifests :
    manifest if strcontains(path, "/jobs/ufo-migrate-")
  ])

  dns_zone_names = ["ufo.ai"]
  # The portal host comes off the variable rather than the module's output: the blob bucket's CORS
  # rule names this origin, and a host built from a module output and fed back into that same
  # module is a dependency cycle terraform refuses.
  shared_host         = "app.${var.apex_host}"
  gateway_origin_host = "origin.${module.platform.hostname}"

  # The shared fleet configuration.
  serve_config = <<-TOML
    [pack]
    name = "assistant_hosted"

    [models]
    auto_model = "z-ai/glm-5.3-flash"

    [memory]
    index_backend = "turbopuffer"

    [research]
    search_provider = "perplexity"

    [browser]
    cdp_provider = "browserbase"

    [flags]
    backend = "flagship"
    # The window a flag answer stands for. Every portal boot read consults these flags, so the
    # cost of a short window is one Cloudflare round trip on the member's first paint; the cost
    # of a long one is how long a flag turned on takes to reach a workspace already reading it.
    cache_ttl_seconds = 900

    [serve]
    host = "0.0.0.0"
    port = 8710
    request_shutdown_seconds = ${local.request_shutdown_seconds}
    graceful_shutdown_seconds = ${local.graceful_shutdown_seconds}

    [database]
    url = "${module.platform.serve_dsn}"

    [blob]
    backend = "s3"
    bucket = "${module.platform.blob_bucket}"
    region = "${var.region}"

    [hub]
    backend = "redis"
    url = "redis://${module.platform.redis_endpoint}:6379/0"

    [terminal]
    backend = "redis"

    [sandbox]
    backend = "e2b"
    proxy_public_url = "https://sandbox-proxy.${module.platform.hostname}"
    ingress_public_url = "https://${module.platform.hostname}"
    ${local.cache_enabled ? "cache_daemon = \"127.0.0.1:9110\"" : ""}
    ${local.preview_enabled ? "preview_service = \"ufo-preview.${local.system_namespace}.svc.cluster.local:8930\"" : ""}

    [connect]
    public_base_url = "https://${local.shared_host}"

    [o11y]
    otlp_endpoint = "http://otel-collector.${local.system_namespace}.svc.cluster.local:4318"
    datadog_check_url = "https://api.us5.datadoghq.com/api/v1/check_run"
    datadog_env = "testing"
  TOML
}

# The shared fleet configuration and process secrets.
resource "kubernetes_secret_v1" "ufo_serve" {
  metadata {
    name      = "ufo-serve"
    namespace = local.system_namespace
  }
  data = {
    "ufo.toml"                 = local.serve_config
    UFO_CONTROL_SERVE_DSN      = module.platform.serve_dsn
    UFO_CREDENTIAL_KEY         = module.platform.serve_credential_key
    UFO_ARTIFACT_TOKEN_SECRET  = module.platform.serve_artifact_token
    UFO_WEB_RUM_APPLICATION_ID = datadog_rum_application.portal.id
    UFO_WEB_RUM_CLIENT_TOKEN   = datadog_rum_application.portal.client_token
  }
  depends_on = [kubernetes_namespace_v1.ufo_system]
}

# The shared public ingress controller.
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
      config               = { proxy-body-size = "100m" }
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

# The hosted service namespace.
resource "kubernetes_namespace_v1" "ufo_system" {
  metadata {
    name = local.system_namespace
    labels = {
      "app.kubernetes.io/managed-by"            = "ufo-control"
      "elbv2.k8s.aws/pod-readiness-gate-inject" = "enabled"
    }
  }
  depends_on = [module.platform]
}

data "aws_ecr_image" "ufo" {
  repository_name = "ufo"
  image_tag       = var.image_tag
}

data "kubectl_file_documents" "hosted" {
  content = templatefile("${path.module}/../../templates/hosted.yaml.tpl", {
    registry                         = module.platform.ecr_registry
    image_tag                        = var.image_tag
    deployment_id                    = "testing"
    namespace                        = local.system_namespace
    apex_host                        = module.platform.hostname
    gateway_origin_host              = local.gateway_origin_host
    site_host                        = module.platform.hostname
    shared_host                      = local.shared_host
    cluster_issuer                   = "letsencrypt"
    ingress_class                    = "nginx"
    bundle_image                     = local.bundle_image
    client_version                   = local.client_version
    e2b_templates                    = var.e2b_templates
    serve_role_arn                   = module.platform.app_s3_role_arn
    ingress_role_arn                 = module.platform.ingress_s3_role_arn
    workload_ha                      = false
    serve_replicas                   = 4
    prestop_seconds                  = local.prestop_seconds
    termination_grace_period_seconds = local.prestop_seconds + local.request_shutdown_seconds + local.graceful_shutdown_seconds + 60
    graceful_shutdown_seconds        = local.graceful_shutdown_seconds

    region        = var.region
    otlp_endpoint = "http://otel-collector.${local.system_namespace}.svc.cluster.local:4318"

    # The sandbox cache (RFC 0032), on for testing with the durable S3 tier so it survives pod rolls.
    cache_enabled     = local.cache_enabled
    cache_s3_bucket   = module.platform.cache_s3_bucket
    cache_s3_role_arn = module.platform.cache_s3_role_arn

    # The preview service (RFC 0037), its own Deployment reached by cluster DNS.
    preview_enabled = local.preview_enabled

    # Onboarding email uses the gateway's SES identity.
    ses_sender           = var.ses_sender
    ses_region           = var.region
    gateway_ses_role_arn = module.platform.gateway_ses_role_arn

    # The signup Slack Connect inviter, off until the operator token is populated and proven.
    slack_connect_enabled = var.slack_connect_enabled ? "true" : "false"
    slack_connect_team_id = var.slack_connect_team_id

    # The join door, which writes a member their own grant once WorkOS verifies their address.
    signup_key = var.signup_key

    # The portal records browser sessions into this deploy's own RUM application, which it reads
    # from the ufo-serve Secret above.
    rum_recording = true
    rum_site      = local.datadog_site
    rum_env       = local.datadog_env
  })
}

data "kubectl_file_documents" "cluster_services" {
  content = templatefile("${path.module}/../../templates/cluster-services.yaml.tpl", {
    acme_server     = var.acme_server
    acme_email      = var.letsencrypt_email
    dns_zones       = jsonencode(local.dns_zone_names)
    region          = var.region
    namespace       = local.system_namespace
    secret_postgres = module.platform.secret_names.postgres
    secret_platform = module.platform.secret_names.platform
    secret_api_keys = module.platform.secret_names.api_keys

    secret_gateway_slack_connect = module.platform.secret_names.gateway_slack_connect
    secret_gateway_workos        = module.platform.secret_names.gateway_workos
  })
}

# Shared OpenTelemetry collection.
data "kubectl_file_documents" "observability" {
  content = templatefile("${path.module}/../../templates/observability.yaml.tpl", {
    namespace     = local.system_namespace
    deployment_id = "testing"
    # 0.114.0 pinned by its amd64 digest: the :0.115.0 tag does not exist and the :0.116.0 build's
    # binary fails to exec on the nodes; this digest is verified running the datadog exporter.
    collector_image = "otel/opentelemetry-collector-contrib@sha256:94ac10da6c15fdad4f8091c4292a8c6814b467cd3bcf575ba2279e9dc6346e63"
    dd_site         = local.datadog_site
    dd_env          = local.datadog_env
    secret_api_keys = module.platform.secret_names.api_keys
  })
}

resource "kubectl_manifest" "ufo_prerequisite" {
  for_each  = local.ufo_prerequisite_manifests
  yaml_body = each.value

  depends_on = [
    module.platform,
    kubernetes_namespace_v1.ufo_system,
    kubernetes_secret_v1.ufo_serve,
  ]
}

# Migration and RLS bootstrap must finish before the service is ready.
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
    kubectl_manifest.ufo_prerequisite,
    kubernetes_secret_v1.ufo_serve,
  ]
}

resource "kubectl_manifest" "ufo" {
  for_each  = local.ufo_workload_manifests
  yaml_body = each.value

  # The workflow's await_rollout.sh is the one rollout gate: each Deployment's newest ReplicaSet
  # fully available. Rollout-completion waits — this one or `kubectl rollout status` — also wait
  # out the old pods' drain (up to terminationGracePeriodSeconds, 700s), and this one dies at the
  # provider's 10m update timeout first, failing healthy rolls. maxUnavailable=0 with maxSurge 100%
  # keeps the old pods serving until every new one is ready.
  wait_for_rollout = false

  depends_on = [kubectl_manifest.ufo_migrate, module.platform]
}
