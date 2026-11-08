variable "cluster_name" {
  description = "Explicit existing foundation cluster for this environment."
  type        = string
  default     = "deployment-project-dev"
  validation {
    condition     = var.cluster_name == "deployment-project-dev"
    error_message = "This backend/account owns only deployment-project-dev."
  }
}

data "aws_eks_cluster" "selected" {
  name = var.cluster_name
}
