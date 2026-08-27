provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# The feature flags this root declares are written by tokens scoped to one Flagship app each, so the
# credential that can turn a portal feature on for every workspace of an environment reaches that
# environment alone — and is not the token the clusters hold for DNS. One deploy carries one of
# them: the other is empty, and its provider is configured and never used.
provider "cloudflare" {
  alias     = "flagship_testing"
  api_token = var.flagship_testing_api_token
}

provider "cloudflare" {
  alias     = "flagship_prod"
  api_token = var.flagship_prod_api_token
}
