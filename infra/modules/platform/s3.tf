
resource "aws_s3_bucket" "blob" {
  bucket = "${local.name}-ufo-blob-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "blob" {
  bucket = aws_s3_bucket.blob.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "blob" {
  bucket                  = aws_s3_bucket.blob.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_cors_configuration" "blob" {
  count  = length(var.blob_origins) > 0 ? 1 : 0
  bucket = aws_s3_bucket.blob.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = var.blob_origins
    allowed_headers = ["content-type", "x-amz-checksum-sha256"]
    expose_headers  = ["etag"]
    max_age_seconds = 3600
  }
}

resource "aws_s3_bucket_versioning" "blob" {
  bucket = aws_s3_bucket.blob.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "blob" {
  bucket = aws_s3_bucket.blob.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket" "cache" {
  bucket = "${local.name}-ufo-cache-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags
}

resource "aws_s3_bucket_ownership_controls" "cache" {
  bucket = aws_s3_bucket.cache.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "cache" {
  bucket                  = aws_s3_bucket.cache.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cache" {
  bucket = aws_s3_bucket.cache.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cache" {
  bucket = aws_s3_bucket.cache.id
  rule {
    id     = "expire-cold-objects"
    status = "Enabled"
    filter {}
    expiration { days = 60 }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "blob" {
  bucket = aws_s3_bucket.blob.id

  rule {
    id     = "expire-workspace-exports"
    status = "Enabled"
    filter {
      tag {
        key   = "ufo-export"
        value = "true"
      }
    }
    expiration { days = 1 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}
