# Image registry. One repo per image we build: the all-in-one platform image, the sandbox-proxy
# image, and the commercial cloud-gateway image (Dockerfile.cloud). CI builds + pushes by commit
# SHA; the chart and the hosted overlay pull by tag.

locals {
  ecr_repositories = ["metalcraft/platform", "metalcraft/sandbox-proxy", "metalcraft/cloud-gateway"]
}

resource "aws_ecr_repository" "this" {
  for_each = toset(local.ecr_repositories)

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
