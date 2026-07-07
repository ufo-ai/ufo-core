module "platform" {
  source = "../../modules/platform"

  name                 = "prod"
  region               = var.region
  hostname             = "flyingobject.ai"
  dns_zone_name        = "flyingobject.ai"
  cloudflare_api_token = var.cloudflare_api_token

  ses_sender           = var.ses_sender
  e2b_sandbox_template = var.e2b_sandbox_template

  # Static cluster-admins (applier-independent — see eks.tf for why creator-perms is off): the account
  # root (a human operating as root keeps kubectl access — the deploy runs from an operator machine
  # while Actions is billing-blocked) and the github-deploy role (for when deploy.yml is revived).
  cluster_admin_principal_arns = [
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/github-deploy",
  ]

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
