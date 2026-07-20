# The shared hosted application: gateway, serve fleet, sandbox proxy, and observability.

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

  # The digest-pinned runtime image used by migration, proxy, and serve.
  bundle_image = "${module.platform.ecr_registry}/ufo@${data.aws_ecr_image.ufo.image_digest}"

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

  # The member-facing host for the shared serve fleet.
  shared_host = "app.${module.platform.hostname}"

  # The shared fleet configuration.
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
    proxy_public_url = "https://sandbox-proxy.${module.platform.hostname}"

    [connect]
    public_base_url = "https://${local.shared_host}"

    [o11y]
    otlp_endpoint = "http://otel-collector.${local.system_namespace}.svc.cluster.local:4318"
  TOML
}

# The shared fleet configuration and process secrets.
resource "kubernetes_secret_v1" "ufo_serve" {
  metadata {
    name      = "ufo-serve"
    namespace = local.system_namespace
  }
  data = {
    "ufo.toml"                = local.serve_config
    UFO_CONTROL_SERVE_DSN     = module.platform.serve_dsn
    UFO_CREDENTIAL_KEY        = module.platform.serve_credential_key
    UFO_ARTIFACT_TOKEN_SECRET = module.platform.serve_artifact_token
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
    name   = local.system_namespace
    labels = { "app.kubernetes.io/managed-by" = "ufo-control" }
  }
  depends_on = [module.platform]
}

data "aws_ecr_image" "ufo" {
  repository_name = "ufo"
  image_tag       = var.image_tag
}

data "kubectl_file_documents" "hosted" {
  content = templatefile("${path.module}/../../templates/hosted.yaml.tpl", {
    registry       = module.platform.ecr_registry
    image_tag      = var.image_tag
    namespace      = local.system_namespace
    apex_host      = module.platform.hostname
    shared_host    = local.shared_host
    cluster_issuer = "letsencrypt"
    ingress_class  = "nginx"
    bundle_image   = local.bundle_image
    serve_role_arn = module.platform.app_s3_role_arn

    # The operator email domain the admin debugger (and its gateway login link) trusts.
    admin_email_domain = "metalcraft.ai"
    region         = var.region
    otlp_endpoint  = "http://otel-collector.${local.system_namespace}.svc.cluster.local:4318"

    # Onboarding email uses the gateway's SES identity.
    ses_sender           = var.ses_sender
    ses_region           = var.region
    gateway_ses_role_arn = module.platform.gateway_ses_role_arn
  })
}

data "kubectl_file_documents" "cluster_services" {
  content = templatefile("${path.module}/../../templates/cluster-services.yaml.tpl", {
    acme_server     = var.acme_server
    acme_email      = var.letsencrypt_email
    dns_zone        = "flyingobject.ai"
    region          = var.region
    namespace       = local.system_namespace
    secret_postgres = module.platform.secret_names.postgres
    secret_platform = module.platform.secret_names.platform
    secret_api_keys = module.platform.secret_names.api_keys
  })
}

# Shared OpenTelemetry collection.
data "kubectl_file_documents" "observability" {
  content = templatefile("${path.module}/../../templates/observability.yaml.tpl", {
    namespace = local.system_namespace
    # 0.114.0 pinned by its amd64 digest: the :0.115.0 tag does not exist and the :0.116.0 build's
    # binary fails to exec on the nodes; this digest is verified running the datadog exporter.
    collector_image = "otel/opentelemetry-collector-contrib@sha256:94ac10da6c15fdad4f8091c4292a8c6814b467cd3bcf575ba2279e9dc6346e63"
    dd_site         = "us5.datadoghq.com"
    dd_env          = "testing"
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

  depends_on = [kubectl_manifest.ufo_migrate]
}
