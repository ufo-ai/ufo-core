provider "aws" {
  region = var.region

  default_tags {
    tags = {
      "flyingobject.ai/environment" = "testing"
      "ManagedBy"                   = "terraform"
    }
  }
}

# Kubernetes/Helm providers authenticate to the cluster the platform module creates,
# via `aws eks get-token` (exec) — deferred to runtime, so first apply works.
provider "kubernetes" {
  host                   = module.platform.cluster_endpoint
  cluster_ca_certificate = base64decode(module.platform.cluster_certificate_authority_data)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.platform.cluster_name, "--region", var.region]
  }
}

provider "helm" {
  kubernetes {
    host                   = module.platform.cluster_endpoint
    cluster_ca_certificate = base64decode(module.platform.cluster_certificate_authority_data)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.platform.cluster_name, "--region", var.region]
    }
  }
}

# kubectl applies the hosted service, issuer, and External Secrets manifests as raw YAML —
# `kubectl apply` adopts them without the cluster being reachable at plan time (unlike the kubernetes
# provider's kubernetes_manifest), so a first apply works.
provider "kubectl" {
  host                   = module.platform.cluster_endpoint
  cluster_ca_certificate = base64decode(module.platform.cluster_certificate_authority_data)
  load_config_file       = false

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.platform.cluster_name, "--region", var.region]
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
