output "kubeconfig_command" {
  value = module.platform.kubeconfig_command
}

output "ecr_repository_urls" {
  value = module.platform.ecr_repository_urls
}

output "store_bucket" {
  value = module.platform.store_bucket
}

output "api_keys_secret_arn" {
  description = "Set real API-key values here, out-of-band."
  value       = module.platform.api_keys_secret_arn
}

output "hostname" {
  value = module.platform.hostname
}
