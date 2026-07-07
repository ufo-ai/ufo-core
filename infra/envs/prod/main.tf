module "platform" {
  source = "../../modules/platform"

  name                 = "prod"
  region               = var.region
  hostname             = "flyingobject.ai"
  dns_zone_name        = "flyingobject.ai"
  letsencrypt_email    = var.letsencrypt_email
  cloudflare_api_token = var.cloudflare_api_token

  image_tag  = var.image_tag
  enable_app = var.enable_app
  chart_path = abspath("${path.module}/../../../charts/metalcraft")

  # Managed vector search: new tenants provision turbopuffer Stores. Requires turbopuffer-api-key in the
  # <prefix>/api-keys Secrets Manager secret before apply, or turbopuffer Stores report BackendUnavailable.
  index_backend = "turbopuffer"

  # OTLP → Datadog. Set datadog-api-key in the <prefix>/api-keys Secrets Manager secret before apply.
  datadog_enabled = true
  datadog_site    = "us5.datadoghq.com"

  # HA across AZs for prod.
  single_nat_gateway        = false
  node_instance_types       = ["m6i.large"]
  node_min_size             = 3
  node_max_size             = 8
  node_desired_size         = 3
  rds_instance_class        = "db.m6g.large"
  rds_multi_az              = true
  rds_allocated_storage     = 50
  rds_max_allocated_storage = 200
  redis_node_type           = "cache.t4g.small"
  redis_num_nodes           = 2
}
