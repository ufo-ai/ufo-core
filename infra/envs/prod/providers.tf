provider "aws" {
  region = var.region

  default_tags {
    tags = {
      "metalcraft.ai/environment" = "prod"
      "ManagedBy"                 = "terraform"
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
