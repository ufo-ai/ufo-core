variable "name" {
  type        = string
  description = "Worker script name; also prefixes the D1 waitlist database."
}

variable "hostname" {
  type        = string
  description = "Apex hostname the worker fronts (the route claims the whole host)."
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

variable "site_base" {
  type        = string
  description = "Site base URL browsers hitting / land on: its own host serves the embedded landing page, every other host redirects to it."
}
