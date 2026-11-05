variable "cluster_name" {
  description = "Existing kind cluster; must match the argocd root. Use separate checkouts/state for different clusters."
  type        = string
  default     = "argocd-postgres"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*$", var.cluster_name))
    error_message = "Use a kind cluster name containing lowercase letters, digits and hyphens."
  }
}

variable "kubeconfig_path" {
  description = "Local kubeconfig file. The ambient current-context is never used."
  type        = string
  default     = "~/.kube/config"
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = "kind-${var.cluster_name}"
}
