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

output "store_bucket" {
  value = aws_s3_bucket.store.id
}

output "rds_endpoint" {
  value = module.rds.db_instance_endpoint
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
