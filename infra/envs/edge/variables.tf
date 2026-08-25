variable "cloudflare_api_token" {
  type      = string
  sensitive = true

  description = "Cloudflare API token for the edge worker: Zone:Read + Workers Routes:Edit + Zone Settings:Edit + Dynamic URL Redirects:Edit + Cache Rules:Edit on the zones, Workers Scripts:Edit + D1:Edit + Queues:Edit on the account. Supplied out-of-band (TF_VAR_cloudflare_api_token / gitignored tfvars), never committed."

  validation {
    condition     = length(var.cloudflare_api_token) >= 20
    error_message = "cloudflare_api_token must be set (a Cloudflare API token with Zone:Read, Workers Routes:Edit, Zone Settings:Edit, Dynamic URL Redirects:Edit, Cache Rules:Edit, Workers Scripts:Edit, D1:Edit, and Queues:Edit). Provide it via TF_VAR_cloudflare_api_token — in CI, the CLOUDFLARE_API_TOKEN GitHub Actions secret."
  }
}
