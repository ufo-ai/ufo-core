terraform {
  # The tfstate bucket + S3-native lock are account-level, created once by metalcraft's infra/bootstrap
  # (external — not managed here). ufo state lives under its own key in that shared bucket.
  backend "s3" {
    bucket       = "metalcraft-tfstate-899147036157"
    key          = "ufo/prod/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
