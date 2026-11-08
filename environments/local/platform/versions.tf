terraform {
  required_version = "~> 1.17.0"

  # This root has local state, independent of AWS.
  backend "local" {}

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
  }
}
