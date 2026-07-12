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

output "sandbox_proxy_url" {
  value = "https://sandbox-proxy.${module.platform.hostname}"
}

output "sandbox_proxy_ca_cert" {
  value = module.platform.egress_ca_cert
}

output "api_keys_secret_arn" {
  description = "Set real API-key values here, out-of-band."
  value       = module.platform.api_keys_secret_arn
}

output "ses_dkim_records" {
  description = "Add these CNAMEs to Cloudflare to verify the SES sending domain."
  value       = module.platform.ses_dkim_records
}

output "hostname" {
  value = module.platform.hostname
}
