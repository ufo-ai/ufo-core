output "kubeconfig_command" {
  value = module.platform.kubeconfig_command
}

output "cluster_name" {
  value = module.platform.cluster_name
}

# The endpoint the fleet dials, which is what the deploy's egress gate must prove. Naming the
# record rather than the host also orders the gate after the proxy that answers it.
output "sandbox_proxy_url" {
  value = "https://${cloudflare_dns_record.sandbox_proxy.name}"
}

output "sandbox_proxy_ca_cert" {
  value = module.platform.egress_ca_cert
}

output "hostname" {
  value = module.platform.hostname
}

output "system_namespace" {
  value = module.platform.system_namespace
}
