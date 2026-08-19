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

output "hostname" {
  value = module.platform.hostname
}

output "shared_host" {
  value = local.shared_host
}

output "system_namespace" {
  value = module.platform.system_namespace
}
