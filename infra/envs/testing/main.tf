module "platform" {
  source = "../../modules/platform"

  name                 = "testing"
  region               = var.region
  hostname             = var.apex_host
  dns_zone_name        = "flyingobject.ai"
  cloudflare_api_token = var.cloudflare_api_token

  ses_sender           = var.ses_sender
  e2b_sandbox_template = var.e2b_sandbox_template

  # Static cluster-admins (applier-independent — see eks.tf for why creator-perms is off): the GitHub
  # Actions deploy role AND the account root (root keeps kubectl access; EKS access entries accept it).
  cluster_admin_principal_arns = [
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/github-deploy",
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
  ]

  # Cost-trimmed, single-AZ data stores for a testing instance.
  single_nat_gateway        = true
  node_instance_types       = ["m6i.large"]
  node_min_size             = 4
  node_max_size             = 6
  node_desired_size         = 4
  rds_instance_class        = "db.t4g.medium"
  rds_multi_az              = false
  rds_allocated_storage     = 20
  rds_max_allocated_storage = 100
  redis_node_type           = "cache.t4g.micro"
  redis_num_nodes           = 1
}
