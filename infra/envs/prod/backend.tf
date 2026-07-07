terraform {
  # State bucket + lock are created by infra/bootstrap (run once). Set the bucket name
  # from its output, then `terraform init`. S3-native locking (use_lockfile) needs no DynamoDB.
  backend "s3" {
    bucket       = "metalcraft-tfstate-899147036157"
    key          = "prod/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
