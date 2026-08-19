# The Identity Center admin permission set. Looked up rather than written down because the role
# carries a generated suffix that changes whenever the permission set is re-provisioned. The ARNs come
# back carrying the reserved SSO path, which is what an access entry wants: it resolves the principal
# to a real IAM role and stores its roleID. (Stripping that path is an aws-auth ConfigMap rule and
# does not apply here — a stripped ARN names no role at all.) Empty in an account with no such
# permission set, which simply grants no entry.
data "aws_iam_roles" "sso_admin" {
  name_regex  = "AWSReservedSSO_AdministratorAccess_.*"
  path_prefix = "/aws-reserved/sso.amazonaws.com/"
}

module "platform" {
  source = "../../modules/platform"

  name                 = "prod"
  region               = var.region
  hostname             = "flyingobject.ai"
  dns_zone_names       = local.dns_zone_names
  cloudflare_api_token = var.cloudflare_api_token

  ses_sender                     = var.ses_sender
  owns_account_resources         = false
  manage_runtime_secret_versions = false

  cluster_admin_principal_arns = concat(
    [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/github-production-deploy",
    ],
    tolist(data.aws_iam_roles.sso_admin.arns),
  )

  # HA across AZs for prod.
  az_count                  = 3
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
