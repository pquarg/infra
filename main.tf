terraform {
  required_version = "~> 1.17.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.23.0, < 7.0.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
  }
}

provider "aws" {
  region              = "eu-north-1"
  profile             = "aws-project"
  allowed_account_ids = ["245361883993"]

  default_tags {
    tags = {
      Project     = "deployment-project"
      Environment = "dev"
      ManagedBy   = "Terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

output "aws_project_id" {
  description = "AWS project targeted by this configuration."
  value       = data.aws_caller_identity.current.account_id
}
