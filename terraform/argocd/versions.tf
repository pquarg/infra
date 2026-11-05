terraform {
  required_version = "~> 1.17.0"

  # This root has local state, independent of both AWS and the platform root.
  backend "local" {}

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
    kubectl = {
      source  = "alekc/kubectl"
      version = "3.0.1"
    }
  }
}
