# Looked up because the role carries a generated suffix that changes whenever the permission set is
# re-provisioned. The reserved SSO path is what an access entry wants; stripping it names no role.
data "aws_iam_roles" "sso_admin" {
  name_regex  = "AWSReservedSSO_AdministratorAccess_.*"
  path_prefix = "/aws-reserved/sso.amazonaws.com/"
}

module "platform" {
  source = "../../modules/platform"

  name                 = "ufo-testing"
  region               = var.region
  hostname             = var.apex_host
  blob_origins         = ["https://${local.shared_host}"]
  dns_zone_names       = local.dns_zone_names
  cloudflare_api_token = var.cloudflare_api_token

  ses_sender             = var.ses_sender
  owns_account_resources = true

  cluster_admin_principal_arns = concat(
    [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/github-deploy",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
    ],
    tolist(data.aws_iam_roles.sso_admin.arns),
  )

  single_nat_gateway        = true
  node_instance_types       = ["m6i.xlarge"]
  node_min_size             = 4
  node_max_size             = 6
  node_desired_size         = 4
  node_disk_size            = 64
  rds_instance_class        = "db.t4g.medium"
  rds_multi_az              = false
  rds_allocated_storage     = 20
  rds_max_allocated_storage = 100
  redis_node_type           = "cache.t4g.micro"
  redis_num_nodes           = 1
}
