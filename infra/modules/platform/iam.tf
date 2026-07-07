# IRSA roles. Each binds an in-cluster ServiceAccount to an AWS IAM role via the
# cluster OIDC provider. Add-on controllers get AWS-managed policies; the app SAs
# get a tight custom policy scoped to this environment's store bucket.

module "irsa_ebs_csi" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name             = "${local.name}-ebs-csi"
  attach_ebs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
  tags = local.tags
}

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


# App service accounts → read/write the store + sandbox-fs buckets, and assume the sandbox-fs role to
# mint per-thread-scoped mount credentials. The framework reads/writes messages.json.lz4 + agent files in
# the sandbox-fs bucket via boto3 with these ambient creds; the sandbox's s3fs mount uses the assumed role.
data "aws_iam_policy_document" "app_s3" {
  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.store.arn, aws_s3_bucket.sandbox_fs.arn]
  }
  statement {
    sid       = "ObjectRW"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.store.arn}/*", "${aws_s3_bucket.sandbox_fs.arn}/*"]
  }
  statement {
    sid       = "AssumeSandboxFsRole"
    actions   = ["sts:AssumeRole"]
    resources = [aws_iam_role.sandbox_fs.arn]
  }
}

# The sandbox-fs mount role: the executor (app-s3 IRSA) assumes this per thread with an inline session
# policy scoping s3 to that thread's prefix, then writes the short-lived credential into the sandbox for
# s3fs. The role grants s3 on the whole bucket; the per-thread session policy minted at AssumeRole narrows
# it. Trust names the app-s3 role by its COMPUTED arn — referencing module.irsa_app_s3.iam_role_arn would
# cycle (irsa_app_s3 → app_s3 policy → this role → irsa_app_s3).
data "aws_iam_policy_document" "sandbox_fs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.name}-app-s3"]
    }
  }
}

data "aws_iam_policy_document" "sandbox_fs" {
  statement {
    sid       = "ListSandboxFsBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.sandbox_fs.arn]
  }
  statement {
    sid       = "ObjectRW"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.sandbox_fs.arn}/*"]
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

  role_name = "${local.name}-app-s3"
  role_policy_arns = merge(
    { s3 = aws_iam_policy.app_s3.arn },
    local.ses_enabled ? { ses = aws_iam_policy.app_ses[0].arn } : {},
  )

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = [for sa in local.s3_service_accounts : "${local.system_namespace}:${sa}"]
    }
  }
  tags = local.tags
}
