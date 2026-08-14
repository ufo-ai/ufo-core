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
  description = "This door's gateway base URL."
}

variable "favicon_svg" {
  type        = string
  description = "Product mark served as the apex favicon."
}

variable "favicon_dark_svg" {
  type        = string
  description = "Product mark served as the apex favicon in dark mode."
}
