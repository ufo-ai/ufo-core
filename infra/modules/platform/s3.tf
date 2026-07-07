# Object store for Store byte blobs (kernel/object_store.py, Boto3ObjectStoreClient).
# App SAs reach it via IRSA (module.irsa_app_s3); the bucket name is surfaced to Store CRs.

resource "aws_s3_bucket" "store" {
  bucket = "${local.name}-metalcraft-store-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags

  # Holds Store byte blobs + payloads — never let a terraform run destroy it.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "store" {
  bucket = aws_s3_bucket.store.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "store" {
  bucket                  = aws_s3_bucket.store.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "store" {
  bucket = aws_s3_bucket.store.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "store" {
  bucket = aws_s3_bucket.store.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}

# Per-thread sandbox-filesystem bucket: each conversation thread's messages.json.lz4 + compactions +
# agent files under <namespace>/threads/<thread_id>/. The framework reads/writes it via boto3 (sandbox-free
# context); the sandbox sees only the thread's workspace/ prefix as /workspace via an s3fs mount with a
# workspace-scoped STS credential (aws_iam_role.sandbox_fs). Separate from the store bucket.

resource "aws_s3_bucket" "sandbox_fs" {
  bucket = "${local.name}-metalcraft-sandbox-fs-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags

  # Holds live conversation context (messages.json.lz4) — never let a terraform run destroy it.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "sandbox_fs" {
  bucket = aws_s3_bucket.sandbox_fs.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "sandbox_fs" {
  bucket                  = aws_s3_bucket.sandbox_fs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "sandbox_fs" {
  bucket = aws_s3_bucket.sandbox_fs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sandbox_fs" {
  bucket = aws_s3_bucket.sandbox_fs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}
