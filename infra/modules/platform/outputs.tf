output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  value = module.eks.cluster_certificate_authority_data
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the cluster."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}

output "ecr_registry" {
  value = local.ecr_registry
}

output "ecr_repository_urls" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "system_namespace" {
  description = "The namespace the hosted processes run in; External Secrets and IRSA target it."
  value       = local.system_namespace
}

output "blob_bucket" {
  description = "The single S3 bucket backing core's [blob] backend (blobs + sandbox mounts)."
  value       = aws_s3_bucket.blob.id
}

output "sandbox_fs_role_arn" {
  description = "STS role the serve fleet assumes to mint per-conversation sandbox mount credentials."
  value       = aws_iam_role.sandbox_fs.arn
}

output "sandbox_proxy_certificate_arn" {
  description = "ACM certificate for the public sandbox-proxy NLB TLS listener."
  value       = aws_acm_certificate.sandbox_proxy.arn
}

output "egress_ca_cert" {
  description = "Trust anchor installed in off-cluster sandboxes and used by the proxy TLS gate."
  value       = tls_self_signed_cert.egress_ca.cert_pem
}

output "app_s3_role_arn" {
  description = "IRSA role annotated on the ufo-serve ServiceAccount — the pod's boto3 reaches the blob bucket and assumes sandbox-fs for the s3fs mount (serve_role_arn)."
  value       = module.irsa_app_s3.iam_role_arn
}

output "gateway_ses_role_arn" {
  description = "Deterministic ufo-gateway IRSA ARN; hosted manifest keys must be plan-known."
  value       = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.gateway_ses_role_name}"
}

# The shared serve fleet's identity and platform keys. Sensitive: the DSN carries
# the ufo_serve password and the keys seal credentials and artifacts. The env's ufo.tf renders
# them into the ufo-serve Secret (config ufo.toml + the two key env vars).
output "serve_dsn" {
  value     = local.serve_dsn
  sensitive = true
}

output "serve_credential_key" {
  value     = local.serve_credential_key
  sensitive = true
}

output "serve_artifact_token" {
  value     = random_password.serve_artifact_token.result
  sensitive = true
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "secret_names" {
  description = "Secrets Manager entries ESO syncs into the cluster."
  value = {
    postgres = aws_secretsmanager_secret.postgres.name
    platform = aws_secretsmanager_secret.platform.name
    api_keys = aws_secretsmanager_secret.api_keys.name
  }
}

output "api_keys_secret_arn" {
  description = "Set real API-key values here, out-of-band (TF seeds it empty)."
  value       = aws_secretsmanager_secret.api_keys.arn
}

output "hostname" {
  value = var.hostname
}
