terraform {
  backend "s3" {
    bucket       = "metalcraft-tfstate-899147036157"
    key          = "ufo/edge/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
