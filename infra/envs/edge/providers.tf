provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# The sending domain and the account's one contact list are shared by every environment, so they are
# owned here beside the DNS that proves them rather than in either environment's platform module.
provider "aws" {
  region = local.ses_region

  default_tags {
    tags = {
      "flyingobject.ai/environment" = "edge"
      "ManagedBy"                   = "terraform"
    }
  }
}

# One token per Flagship app, so the credential that turns a feature on reaches one environment alone.
# One deploy carries one of them: the other is empty, and its provider is configured and never used.
provider "cloudflare" {
  alias     = "flagship_testing"
  api_token = var.flagship_testing_api_token
}

provider "cloudflare" {
  alias     = "flagship_prod"
  api_token = var.flagship_prod_api_token
}
