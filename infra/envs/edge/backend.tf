terraform {
  # Same account-level tfstate bucket as the env roots (created by metalcraft's infra/bootstrap).
  # The edge is zone-level — one flyingobject.ai zone fronting every env — so it gets its own state
  # under ufo/edge, applied independently of any env's platform.
  backend "s3" {
    bucket       = "metalcraft-tfstate-899147036157"
    key          = "ufo/edge/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
