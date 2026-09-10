terraform {
  required_version = ">= 1.9"

  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.95" }
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.12" }
  }
}
