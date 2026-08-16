# IRSA roles. Each binds an in-cluster ServiceAccount to an AWS IAM role via the
# cluster OIDC provider. Add-on controllers get AWS-managed policies; the app SAs
# get a tight custom policy scoped to this environment's store bucket.

module "irsa_lb_controller" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name                              = "${local.name}-lb-controller"
  attach_load_balancer_controller_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }
  tags = local.tags
}

module "irsa_external_secrets" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name                      = "${local.name}-external-secrets"
  attach_external_secrets_policy = true
  # Scope to this environment's secrets only.
  external_secrets_secrets_manager_arns = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${local.secret_prefix}/*"]

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["external-secrets:external-secrets"]
    }
  }
  tags = local.tags
}


data "aws_iam_policy_document" "app_s3" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.blob.arn]
  }
  statement {
    sid       = "ObjectRW"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.blob.arn}/*"]
  }
}

resource "aws_iam_policy" "app_s3" {
  name   = local.app_s3_role_name
  policy = data.aws_iam_policy_document.app_s3.json
  tags   = local.tags
}

module "irsa_app_s3" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name        = local.app_s3_role_name
  role_policy_arns = { s3 = aws_iam_policy.app_s3.arn }

  assume_role_condition_test = "StringLike"
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["ufo-*:ufo-serve"]
    }
  }
  tags = local.tags
}


# The sandbox cache daemon runs in the proxy pod and reaches only the cache bucket — a separate,
# tighter grant than serve's app_s3 (the proxy holds no blob access). Scoped to this env's cache
# bucket alone.
data "aws_iam_policy_document" "cache_s3" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.cache.arn]
  }
  statement {
    sid       = "ObjectRW"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.cache.arn}/*"]
  }
}

resource "aws_iam_policy" "cache_s3" {
  name   = local.cache_s3_role_name
  policy = data.aws_iam_policy_document.cache_s3.json
  tags   = local.tags
}

module "irsa_cache_s3" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name        = local.cache_s3_role_name
  role_policy_arns = { s3 = aws_iam_policy.cache_s3.arn }

  assume_role_condition_test = "StringLike"
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["ufo-*:ufo-sandbox-proxy"]
    }
  }
  tags = local.tags
}
