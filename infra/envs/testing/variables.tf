variable "region" {
  type    = string
  default = "us-east-1"
}

variable "apex_host" {
  type        = string
  default     = "testing.flyingobject.ai"
  description = "Apex FQDN the platform (onboarding gateway) workspace serves."
}

variable "tenant_base_domain" {
  type        = string
  default     = "testing.flyingobject.ai"
  description = "Tenants are served at <name>.<tenant_base_domain>; consumed by the onboarding gateway when it assembles each tenant's host (surfaced as an output for that wiring)."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag for the ufo-control image the control-plane deployment pulls (a pushed git short SHA). deploy.yml passes -var image_tag."
}

variable "letsencrypt_email" {
  type        = string
  default     = "ops@flyingobject.ai"
  description = "ACME account contact for cert expiry notices. Override via tfvars with a monitored mailbox."
}

variable "acme_server" {
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
  description = "ACME directory the cert-manager ClusterIssuer uses. Point at staging to avoid rate limits."
}

variable "ses_sender" {
  type        = string
  default     = "no-reply@flyingobject.ai"
  description = "From address for onboarding email; its domain is verified as the SES sending identity."
}

variable "e2b_sandbox_template" {
  type        = string
  default     = "ufo-sbx"
  description = "E2B template id the runtime launches sandboxes from."
}

variable "cloudflare_api_token" {
  type        = string
  sensitive   = true
  description = "Cloudflare API token (Zone:DNS:Edit on flyingobject.ai) for external-dns + cert-manager DNS-01. Supply via TF_VAR_cloudflare_api_token; never commit."
}
