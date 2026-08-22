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

  name                 = "ufo-testing"
  region               = var.region
  hostname             = var.apex_host
  dns_zone_names       = local.dns_zone_names
  cloudflare_api_token = var.cloudflare_api_token

  ses_sender             = var.ses_sender
  owns_account_resources = true

  # Static cluster-admins (applier-independent — see eks.tf for why creator-perms is off): the GitHub
  # Actions deploy role, the account root, and the Identity Center admin permission set. An entry for
  # the root principal does NOT cover a role assumed through it, so without the SSO role every human
  # kubectl is rejected and cluster access has to be laundered through the deploy role.
  cluster_admin_principal_arns = concat(
    [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/github-deploy",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
    ],
    tolist(data.aws_iam_roles.sso_admin.arns),
  )

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
