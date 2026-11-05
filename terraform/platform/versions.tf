terraform {
  required_version = "~> 1.17.0"

  # Apply only after Argo CD's CRDs exist. No AWS backend or credentials.
  backend "local" {}

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
  }
}
