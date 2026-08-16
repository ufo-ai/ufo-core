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

output "system_namespace" {
  description = "The namespace the hosted processes run in; External Secrets and IRSA target it."
  value       = local.system_namespace
}

output "blob_bucket" {
  description = "The single S3 bucket backing core's [blob] backend (transcripts + artifacts)."
  value       = aws_s3_bucket.blob.id
}

output "cache_s3_bucket" {
  description = "The S3 bucket backing the sandbox cache daemon's durable tier (RFC 0032)."
  # Built from plan-known inputs, not aws_s3_bucket.cache.id: this name renders into the hosted
  # manifest, whose keys feed a for_each that must be known at plan time — the same reason the IRSA
  # ARNs above are hand-built. `.id` is unknown until apply on a fresh bucket and breaks the plan.
  value = "${local.name}-ufo-cache-${data.aws_caller_identity.current.account_id}"
}

output "cache_s3_role_arn" {
  description = "IRSA role annotated on the ufo-sandbox-proxy ServiceAccount for cache bucket access."
  value       = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.cache_s3_role_name}"
}

output "sandbox_proxy_certificate_arn" {
  description = "Validated public ACM certificate for the sandbox-proxy NLB TLS listener."
  value       = aws_acm_certificate_validation.sandbox_proxy_public.certificate_arn
}

output "egress_ca_cert" {
  description = "Trust anchor for the leaves the proxy mints inside the tunnel, installed in off-cluster sandboxes and used by the proxy TLS gate."
  value       = tls_self_signed_cert.egress_ca.cert_pem
}

output "app_s3_role_arn" {
  description = "IRSA role annotated on the ufo-serve ServiceAccount for blob bucket access."
  value       = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.app_s3_role_name}"
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
    postgres              = aws_secretsmanager_secret.postgres.name
    platform              = aws_secretsmanager_secret.platform.name
    api_keys              = aws_secretsmanager_secret.api_keys.name
    gateway_slack_connect = aws_secretsmanager_secret.gateway_slack_connect.name
    gateway_workos        = data.aws_secretsmanager_secret.gateway_workos.name
  }
}

output "api_keys_secret_id" {
  value = aws_secretsmanager_secret.api_keys.id
}

output "gateway_secret_id" {
  value = aws_secretsmanager_secret.gateway_slack_connect.id
}

output "hostname" {
  value = var.hostname
}

output "db_instance_identifier" {
  description = "The RDS instance's own identifier, which is how CloudWatch tags every metric it reports for the database."
  value       = module.rds.db_instance_identifier
}

output "vpc_id" {
  value       = module.vpc.vpc_id
  description = "The platform VPC."
}

output "vpc_cidr_block" {
  value       = module.vpc.vpc_cidr_block
  description = "The platform VPC CIDR, routed from any peered network."
}

output "private_route_table_ids" {
  value       = module.vpc.private_route_table_ids
  description = "Private route tables that carry return traffic to a peered network."
}

output "rds_security_group_id" {
  value       = aws_security_group.rds.id
  description = "The Postgres security group, so a peered network can be admitted to it."
}

output "secret_arns" {
  description = "Secrets Manager entries the colocated proxy reads, by their primary-region ARN."
  value = {
    postgres = aws_secretsmanager_secret.postgres.arn
    platform = aws_secretsmanager_secret.platform.arn
    api_keys = aws_secretsmanager_secret.api_keys.arn
  }
}
