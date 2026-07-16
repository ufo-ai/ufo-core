terraform {
  required_version = ">= 1.9"

  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.95" }
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.12" }
    helm       = { source = "hashicorp/helm", version = "~> 2.17" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.35" }
    kubectl    = { source = "alekc/kubectl", version = "~> 2.1" }
    datadog    = { source = "DataDog/datadog", version = "~> 3.50" }
  }
}
