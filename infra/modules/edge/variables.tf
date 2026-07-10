variable "name" {
  type        = string
  description = "Worker script name; also prefixes the D1 waitlist database."
}

variable "hostname" {
  type        = string
  description = "Apex hostname the worker fronts (routes are claimed per-path on this host)."
}

variable "zone_id" {
  type        = string
  description = "Cloudflare zone that owns hostname."
}

variable "account_id" {
  type        = string
  description = "Cloudflare account that owns the worker script and D1 database."
}

variable "origin_base" {
  type        = string
  description = "Gateway base URL /install proxies the stamped client script from (its /ufo route)."
}
