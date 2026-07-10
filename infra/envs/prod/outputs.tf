output "kubeconfig_command" {
  value = module.platform.kubeconfig_command
}

output "ecr_repository_urls" {
  value = module.platform.ecr_repository_urls
}

output "blob_bucket" {
  value = module.platform.blob_bucket
}

output "sandbox_fs_role_arn" {
  description = "Assumed per conversation to mint scoped mount credentials; the deploy mount gate reads it."
  value       = module.platform.sandbox_fs_role_arn
}

output "api_keys_secret_arn" {
  description = "Set real API-key values here, out-of-band."
  value       = module.platform.api_keys_secret_arn
}

output "hostname" {
  value = module.platform.hostname
}
