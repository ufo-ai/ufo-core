variable "region" {
  type    = string
  default = "us-east-1"
}

variable "apex_host" {
  type        = string
  default     = "testing.flyingobject.ai"
  description = "Apex FQDN the platform (onboarding gateway) workspace serves."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag for the ufo-control gateway image. deploy.yml passes the git short SHA."
}

variable "e2b_template" {
  type = string

  validation {
    condition     = var.e2b_template != ""
    error_message = "e2b_template must not be empty."
  }
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

variable "cloudflare_api_token" {
  type        = string
  sensitive   = true
  description = "Cloudflare API token (Zone:DNS:Edit on flyingobject.ai) for external-dns + cert-manager DNS-01. Supply via TF_VAR_cloudflare_api_token; never commit."
}

variable "slack_connect_enabled" {
  type        = bool
  default     = false
  description = "Send each newly invited customer a Slack Connect invitation from the operator Slack workspace. Requires the out-of-band bot token and slack_connect_team_id."
}

variable "slack_connect_team_id" {
  type        = string
  default     = ""
  description = "Team ID of UFO's own operator Slack workspace. The gateway refuses to mutate channels in any other team."
}
