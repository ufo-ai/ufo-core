variable "name" {
  type        = string
  description = "Worker script name."
}

variable "hostname" {
  type        = string
  description = "Hostname whose documentation paths the worker answers."
}

variable "dist" {
  type        = string
  description = "Built docs site the worker serves as its assets."
}

variable "routes" {
  type        = set(string)
  description = "Path patterns the documentation worker answers on its hostname."
}

variable "zone_id" {
  type        = string
  description = "Cloudflare zone that owns hostname."
}

variable "account_id" {
  type        = string
  description = "Cloudflare account that owns the worker script."
}
