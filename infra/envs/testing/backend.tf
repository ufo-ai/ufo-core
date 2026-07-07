terraform {
  # The tfstate bucket + S3-native lock are account-level, created once by metalcraft's infra/bootstrap
  # (external — not managed here). ufo's testing state lives under its own key, separate from
  # metalcraft's testing state (key testing/*), so rollback = re-apply metalcraft's testing config.
  backend "s3" {
    bucket       = "metalcraft-tfstate-899147036157"
    key          = "ufo/testing/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
