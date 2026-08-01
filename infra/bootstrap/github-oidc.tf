# GitHub Actions → AWS via OIDC (no static keys). Account-global, so it lives in bootstrap alongside
# the state bucket. The deploy workflow assumes `github_deploy` to push images to ECR and run
# `terraform apply`. Trust is scoped to this repo's main branch (apply) and pull requests (plan).

variable "github_repo" {
  type        = string
  default     = "metalcraftai/ufo"
  description = "owner/repo allowed to assume the deploy role via OIDC."
}

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_repo}:pull_request",
      ]
    }
  }

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
  description        = "Assumed by GitHub Actions (${var.github_repo}) to build/push images and terraform apply."
}

# terraform apply manages the whole stack (EKS, RDS, IAM/IRSA, S3, ECR, Secrets Manager),
# so the deploy role needs broad access. Trust is the control surface (one repo, main + PRs); tighten
# to a least-privilege policy as a follow-up if the account is shared more widely.
resource "aws_iam_role_policy_attachment" "github_deploy_admin" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "github_deploy_role_arn" {
  description = "Set as the role-to-assume in the GitHub Actions deploy workflow."
  value       = aws_iam_role.github_deploy.arn
}
