output "kubeconfig_command" {
  value = module.platform.kubeconfig_command
}

output "cluster_name" {
  value = module.platform.cluster_name
}

output "sandbox_proxy_url" {
  value = "https://sandbox-proxy.${module.platform.hostname}"
}

output "sandbox_proxy_ca_cert" {
  value = module.platform.egress_ca_cert
}

output "api_keys_secret_id" {
  value = module.platform.api_keys_secret_id
}

output "gateway_secret_id" {
  value = module.platform.gateway_secret_id
}

output "hostname" {
  value = module.platform.hostname
}

output "system_namespace" {
  value = module.platform.system_namespace
}
