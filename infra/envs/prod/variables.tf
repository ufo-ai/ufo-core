variable "region" {
  type    = string
  default = "us-east-1"
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "metalcraft platform image tag (set a pushed git SHA before enabling the app)."
}

variable "enable_app" {
  type        = bool
  default     = false
  description = "Flip to true once charts/metalcraft exists and the image is pushed to ECR."
}

variable "letsencrypt_email" {
  type    = string
  default = "alex@metalcraft.ai"
}

variable "cloudflare_api_token" {
  type        = string
  sensitive   = true
  description = "Cloudflare API token (Zone:DNS:Edit on flyingobject.ai) for external-dns. Supply via TF_VAR_cloudflare_api_token; never commit."
}
