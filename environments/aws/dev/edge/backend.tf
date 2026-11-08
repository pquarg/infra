terraform {
  backend "s3" {
    bucket              = "deployment-project-tfstate-245361883993-eu-north-1"
    key                 = "dev/edge/terraform.tfstate"
    region              = "eu-north-1"
    profile             = "aws-project"
    allowed_account_ids = ["245361883993"]
    encrypt             = true
    use_lockfile        = true
  }
}
