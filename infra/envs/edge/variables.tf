variable "cloudflare_api_token" {
  type      = string
  sensitive = true

  description = "Cloudflare API token for the edge worker: Zone:Read + Workers Routes:Edit + Zone Settings:Edit + Dynamic URL Redirects:Edit + Cache Rules:Edit on the zones, Workers Scripts:Edit + D1:Edit + Queues:Edit on the account. Supplied out-of-band (TF_VAR_cloudflare_api_token / gitignored tfvars), never committed."

  validation {
    condition     = length(var.cloudflare_api_token) >= 20
    error_message = "cloudflare_api_token must be set (a Cloudflare API token with Zone:Read, Workers Routes:Edit, Zone Settings:Edit, Dynamic URL Redirects:Edit, Cache Rules:Edit, Workers Scripts:Edit, D1:Edit, and Queues:Edit). Provide it via TF_VAR_cloudflare_api_token — in CI, the CLOUDFLARE_API_TOKEN GitHub Actions secret."
  }
}

variable "flagship_testing_api_token" {
  type      = string
  sensitive = true
  default   = ""

  description = "Cloudflare API token scoped to the ufo-testing Flagship app alone (Flagship App Write on that one app), which writes that environment's feature flags and nothing else. Supplied by the testing deploy as TF_VAR_flagship_testing_api_token from the FLAGSHIP_TESTING_API_TOKEN repository secret. Empty in a plan that targets no flag of that environment."
}

variable "flagship_prod_api_token" {
  type      = string
  sensitive = true
  default   = ""

  description = "Cloudflare API token scoped to the ufo-prod Flagship app alone, which writes production's feature flags and nothing else. Supplied by the production deploy as TF_VAR_flagship_prod_api_token from the FLAGSHIP_PROD_API_TOKEN production environment secret, so no pull request and no testing deploy can move a production feature."
}
