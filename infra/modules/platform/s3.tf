# One S3 bucket backs core's [blob] backend="s3" (RFC 0011 §3): blob bytes AND per-conversation
# sandbox files, all uuid-addressed and workspace_id-prefixed. Serve pods reach it ambiently via
# IRSA (module.irsa_app_s3); the sandbox sees only its conversation prefix as /workspace through an
# s3fs mount with a conversation-scoped STS credential (aws_iam_role.sandbox_fs, sandbox/fs_creds.py).
# Its name is rendered into the shared runtime config.

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
