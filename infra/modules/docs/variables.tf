variable "name" {
  type        = string
  description = "Worker script name."
}

variable "hostname" {
  type        = string
  description = "Hostname the docs site answers on (the custom domain claims the whole host)."
}

variable "dist" {
  type        = string
  description = "Built docs site the worker serves as its assets."
}

variable "zone_id" {
  type        = string
  description = "Cloudflare zone that owns hostname."
}

variable "account_id" {
  type        = string
  description = "Cloudflare account that owns the worker script."
}
