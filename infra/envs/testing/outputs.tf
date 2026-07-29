output "kubeconfig_command" {
  value = module.platform.kubeconfig_command
}

output "cluster_name" {
  value = module.platform.cluster_name
}

output "ecr_repository_urls" {
  value = module.platform.ecr_repository_urls
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

output "hostname" {
  value = module.platform.hostname
}

output "system_namespace" {
  value = module.platform.system_namespace
}
