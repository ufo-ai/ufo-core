terraform {
  # The tfstate bucket and S3-native lock are account-level, created once by metalcraft's
  # infra/bootstrap — external, not managed here. ufo's testing state lives under its own key.
  backend "s3" {
    bucket       = "metalcraft-tfstate-899147036157"
    key          = "ufo/testing/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
