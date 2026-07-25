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

# The sandbox-fs mount role: the proxy assumes this per credential fetch with an inline session
# policy scoping S3 to that conversation's prefix. The role grants S3 on the whole bucket; the
# per-conversation session policy minted at AssumeRole narrows it.
data "aws_iam_policy_document" "sandbox_fs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [module.irsa_sandbox_proxy.iam_role_arn]
    }
  }
}

data "aws_iam_policy_document" "sandbox_fs" {
  statement {
    sid       = "ListBlobBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.blob.arn]
  }
  statement {
    sid       = "ObjectRW"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.blob.arn}/*"]
  }
}

resource "aws_iam_role" "sandbox_fs" {
  name                 = "${local.name}-sandbox-fs"
  assume_role_policy   = data.aws_iam_policy_document.sandbox_fs_trust.json
  max_session_duration = 3600
  tags                 = local.tags
}

resource "aws_iam_role_policy" "sandbox_fs" {
  name   = "sandbox-fs-s3"
  role   = aws_iam_role.sandbox_fs.id
  policy = data.aws_iam_policy_document.sandbox_fs.json
}

resource "aws_iam_policy" "app_s3" {
  name   = "${local.name}-app-s3"
  policy = data.aws_iam_policy_document.app_s3.json
  tags   = local.tags
}

module "irsa_app_s3" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name        = "${local.name}-app-s3"
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

data "aws_iam_policy_document" "sandbox_proxy" {
  statement {
    actions   = ["sts:AssumeRole"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.name}-sandbox-fs"]
  }
}

resource "aws_iam_policy" "sandbox_proxy" {
  name   = "${local.name}-sandbox-proxy"
  policy = data.aws_iam_policy_document.sandbox_proxy.json
  tags   = local.tags
}

module "irsa_sandbox_proxy" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name        = "${local.name}-sandbox-proxy"
  role_policy_arns = { assume = aws_iam_policy.sandbox_proxy.arn }

  assume_role_condition_test = "StringLike"
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["ufo-*:ufo-sandbox-proxy"]
    }
  }
  tags = local.tags
}
