variable "github_repo" {
  type    = string
  default = "metalcraftai/ufo"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      "app.kubernetes.io/part-of" = "ufo"
      "ManagedBy"                 = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "github_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:environment:production"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:ref"
      values   = ["refs/heads/main"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:environment"
      values   = ["production"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:workflow"
      values   = ["Deploy (testing)"]
    }
  }
}

resource "aws_iam_role" "github_production_deploy" {
  name               = "github-production-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
  description        = "Assumed by the GitHub production environment to deploy production."
}

resource "aws_iam_role_policy_attachment" "administrator" {
  role       = aws_iam_role.github_production_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
