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

data "aws_security_group" "myfastapiapp_alb" {
  filter {
    name   = "vpc-id"
    values = [data.aws_eks_cluster.selected.vpc_config[0].vpc_id]
  }
  filter {
    name   = "group-name"
    values = ["deployment-project-dev-myfastapiapp-alb"]
  }
}
