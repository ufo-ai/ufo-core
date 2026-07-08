# One-time bootstrap: the S3 bucket that holds remote state for the env root modules.
# Runs with LOCAL state (no backend). S3-native locking means no DynamoDB table.
# After apply, copy the bucket name into each env's backend.tf and `terraform init`.

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.95" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
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

# Bucket names are immutable — renaming means creating a new bucket and migrating every env's
# state, so the name keeps its original prefix.
resource "aws_s3_bucket" "state" {
  bucket = "metalcraft-tfstate-${data.aws_caller_identity.current.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

variable "region" {
  type    = string
  default = "us-east-1"
}

output "state_bucket" {
  description = "Put this in each env's backend.tf `bucket`."
  value       = aws_s3_bucket.state.id
}
