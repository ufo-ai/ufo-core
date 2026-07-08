# The ufo application layer on top of the platform substrate (RFC 0011 §6, testing-first): shared
# ingress (ingress-nginx), the cert-manager ClusterIssuer, the control plane (Tenant CRD + operator +
# API), its platform config, and the External Secrets that feed the operator and every tenant pod.
# Same transformation as envs/prod; only the hosts + sizing differ. Applying this replaces the
# metalcraft app on the testing substrate (RFC 0011 §6); ufo mints its own `ufo` DB + roles.

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

  # PlatformConfig (ufo_control.platform) the operator mounts at /config/platform.toml. Names the
  # shared backing services + cluster facts the reconciler overlays onto every tenant's core config.
  platform_config = <<-TOML
    chart_path = "/charts/ufo-tenant"
    registry = "${module.platform.ecr_registry}"

    tenant_postgres_host = "${module.platform.rds_endpoint}"
    redis_url = "redis://${module.platform.redis_endpoint}:6379/0"

    blob_bucket = "${module.platform.blob_bucket}"
    blob_region = "${var.region}"
    blob_sts_role_arn = "${module.platform.sandbox_fs_role_arn}"

    ingress_class = "nginx"
    cluster_issuer = "letsencrypt"
    platform_secret = "ufo-platform-secrets"
  TOML

  ufo_manifests = merge(
    data.kubectl_file_documents.tenant_crd.manifests,
    data.kubectl_file_documents.control_plane.manifests,
    data.kubectl_file_documents.cluster_services.manifests,
  )
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
# the control plane + cluster services are templated with the image + secret names.
data "kubectl_file_documents" "tenant_crd" {
  content = file("${path.module}/../../../control/deploy/crds/tenant.yaml")
}

data "kubectl_file_documents" "control_plane" {
  content = templatefile("${path.module}/ufo/control-plane.yaml.tpl", {
    registry       = module.platform.ecr_registry
    image_tag      = var.image_tag
    namespace      = local.system_namespace
    apex_host      = var.apex_host
    base_domain    = var.tenant_base_domain
    cluster_issuer = "letsencrypt"
    ingress_class  = "nginx"
  })
}

data "kubectl_file_documents" "cluster_services" {
  content = templatefile("${path.module}/ufo/cluster-services.yaml.tpl", {
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

resource "kubectl_manifest" "ufo" {
  for_each  = local.ufo_manifests
  yaml_body = each.value

  depends_on = [
    module.platform,
    kubernetes_namespace_v1.ufo_system,
    kubernetes_config_map_v1.ufo_control_platform,
  ]
}
