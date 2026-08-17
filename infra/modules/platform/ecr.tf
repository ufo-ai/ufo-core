# Image registry for the runtime bundle, hosted gateway, sandbox cache daemon, and the client
# binaries of a client tree.

locals {
  ecr_repositories = ["ufo", "ufo-control", "ufo-cache", "ufo-egress", "ufo-clientbin"]
}

resource "aws_ecr_repository" "this" {
  for_each = var.owns_account_resources ? toset(local.ecr_repositories) : toset([])

  name                 = each.value
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration { scan_on_push = true }

  encryption_configuration { encryption_type = "AES256" }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
