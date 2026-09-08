provider "cloudflare" {
  api_token = var.cloudflare_api_token
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
