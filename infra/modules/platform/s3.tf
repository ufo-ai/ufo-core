# One S3 bucket backs core's [blob] backend="s3" (RFC 0011 §3): transcripts, compaction records,
# and shared artifacts, all uuid-addressed and workspace_id-prefixed. Serve pods reach it ambiently
# via IRSA (module.irsa_app_s3); the sandbox holds no credential for it — a shared file arrives as
# one presigned PUT serve mints. Its name is rendered into the shared runtime config.

resource "aws_s3_bucket" "blob" {
  bucket = "${local.name}-ufo-blob-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags

  # Holds blob bytes + live conversation context — never let a terraform run destroy it.
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

# A web attachment travels as one presigned PUT the member's own browser sends, from the portal
# page to this bucket — a different origin, and a PUT carrying `x-amz-checksum-sha256`, which is
# not a safelisted header, so the browser asks permission first. Without this rule S3 refuses that
# preflight and every attachment falls back to the composer body the presigned path exists to keep
# the bytes out of. The rule grants no read: only a PUT from a named portal origin, whose URL the
# surface already signed for exactly one key, one length and one checksum.
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

# The sandbox cache's durable tier (RFC 0032): git bundles under git/<principal>/ (private mirrors
# namespaced by workspace principal, public ones under git/public/) and public package artifacts
# under pkg/<host>/. One bucket holds every workspace's warm cache without cross-tenant collision
# because the key path carries the principal the control plane resolves per request, not the sandbox.
# The proxy pod's SA reaches it via IRSA (module.irsa_cache_s3); enabled per env by `cache_s3_bucket`.
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

# The daemon's LRU evicts local disk only, never S3, so the durable tier is bounded here instead.
# An object unread for the window is re-fetchable from origin (git re-clones, packages and LFS
# objects re-download);
# a hot git bundle is re-snapshotted on its interval, which resets its age, so only genuinely cold
# objects expire. Without this the shared pkg/ prefix — every artifact ever fetched fleet-wide —
# grows without limit.
resource "aws_s3_bucket_lifecycle_configuration" "cache" {
  bucket = aws_s3_bucket.cache.id
  rule {
    id     = "expire-cold-objects"
    status = "Enabled"
    filter {}
    expiration { days = 60 }
  }
}
