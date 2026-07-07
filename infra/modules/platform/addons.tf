# In-cluster controllers that must exist before the metalcraft chart. Installed as
# upstream Helm releases (not our chart). Versions are pinned — review on upgrade.

resource "helm_release" "aws_load_balancer_controller" {
  name             = "aws-load-balancer-controller"
  repository       = "https://aws.github.io/eks-charts"
  chart            = "aws-load-balancer-controller"
  version          = "1.10.1"
  namespace        = "kube-system"
  create_namespace = false

  set {
    name  = "clusterName"
    value = module.eks.cluster_name
  }
  set {
    name  = "region"
    value = var.region
  }
  set {
    name  = "vpcId"
    value = module.vpc.vpc_id
  }
  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = module.irsa_lb_controller.iam_role_arn
  }

  depends_on = [module.eks]
}

resource "helm_release" "metrics_server" {
  name             = "metrics-server"
  repository       = "https://kubernetes-sigs.github.io/metrics-server/"
  chart            = "metrics-server"
  version          = "3.12.2"
  namespace        = "kube-system"
  create_namespace = false

  depends_on = [module.eks]
}

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = "v1.16.2"
  namespace        = "cert-manager"
  create_namespace = true

  set {
    name  = "crds.enabled"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "cert-manager"
  }

  depends_on = [module.eks]
}

# The chart's ClusterIssuer solves DNS-01 through this token (same token external-dns uses).
# Ordered after the release so helm owns the namespace.
resource "kubernetes_secret" "cloudflare_api_token_cert_manager" {
  metadata {
    name      = "cloudflare-api-token"
    namespace = "cert-manager"
  }
  data       = { cloudflare_api_token = var.cloudflare_api_token }
  type       = "Opaque"
  depends_on = [helm_release.cert_manager]
}

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  version          = "0.10.7"
  namespace        = "external-secrets"
  create_namespace = true

  set {
    name  = "installCRDs"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "external-secrets"
  }
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = module.irsa_external_secrets.iam_role_arn
  }

  depends_on = [module.eks]
}

# external-dns publishes records into the authoritative Cloudflare zone for the domain — Cloudflare
# fronts the gateway hostname as a DDoS/WAF edge, so DNS lives there. It authenticates with a scoped
# Cloudflare API token delivered as a Kubernetes Secret — no IRSA. Helm owns the namespace
# (create_namespace is idempotent on an existing one); the token Secret is applied into it afterward.
# wait=false because external-dns can't go Ready until that Secret exists, and the Secret is ordered
# after this release — blocking on readiness here would deadlock a fresh install.
resource "helm_release" "external_dns" {
  name             = "external-dns"
  repository       = "https://kubernetes-sigs.github.io/external-dns/"
  chart            = "external-dns"
  version          = "1.15.0"
  namespace        = "external-dns"
  create_namespace = true
  wait             = false

  values = [yamlencode({
    provider       = { name = "cloudflare" }
    policy         = "sync"
    txtOwnerId     = module.eks.cluster_name
    domainFilters  = [var.dns_zone_name]
    serviceAccount = { name = "external-dns" }
    env = [{
      name = "CF_API_TOKEN"
      valueFrom = {
        secretKeyRef = {
          name = "cloudflare-api-token"
          key  = "cloudflare_api_token"
        }
      }
    }]
  })]

  depends_on = [module.eks]
}

resource "kubernetes_secret" "cloudflare_api_token" {
  metadata {
    name      = "cloudflare-api-token"
    namespace = "external-dns"
  }
  data       = { cloudflare_api_token = var.cloudflare_api_token }
  type       = "Opaque"
  depends_on = [helm_release.external_dns]
}

# Envoy Gateway implements GatewayClass "metalcraft" (the chart creates the GatewayClass
# pointing at this controller). Fronted by an NLB the AWS LB Controller provisions.
resource "helm_release" "envoy_gateway" {
  name             = "envoy-gateway"
  repository       = "oci://docker.io/envoyproxy"
  chart            = "gateway-helm"
  version          = "v1.2.4"
  namespace        = "envoy-gateway-system"
  create_namespace = true

  depends_on = [module.eks]
}
