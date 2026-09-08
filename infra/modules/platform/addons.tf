
resource "helm_release" "aws_load_balancer_controller" {
  name             = "aws-load-balancer-controller"
  repository       = "https://aws.github.io/eks-charts"
  chart            = "aws-load-balancer-controller"
  version          = "1.10.1"
  namespace        = "kube-system"
  create_namespace = false
  wait             = true
  atomic           = true

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

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = "v1.16.2"
  namespace        = "cert-manager"
  create_namespace = true
  atomic           = true

  set {
    name  = "crds.enabled"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "cert-manager"
  }

  depends_on = [helm_release.aws_load_balancer_controller]
}

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
  atomic           = true

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

  depends_on = [helm_release.aws_load_balancer_controller]
}

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
    sources        = ["service", "ingress"]
    txtOwnerId     = module.eks.cluster_name
    domainFilters  = var.dns_zone_names
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

  depends_on = [helm_release.aws_load_balancer_controller]
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
