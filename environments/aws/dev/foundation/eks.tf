variable "eks_admin_cidrs" {
  description = "Public IPv4 /32 addresses allowed to reach the Kubernetes API and the CloudFront app."
  type        = list(string)

  validation {
    condition = length(var.eks_admin_cidrs) > 0 && alltrue([
      for cidr in var.eks_admin_cidrs :
      can(cidrnetmask(cidr)) && endswith(cidr, "/32")
    ])
    error_message = "Provide at least one public IPv4 address with a /32 suffix."
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.26.0"

  name                = "deployment-project-dev"
  kubernetes_version  = "1.36"
  authentication_mode = "API"

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.private_subnets

  endpoint_private_access      = true
  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.eks_admin_cidrs

  # Creates an EKS access entry for the IAM role behind the Terraform login.
  enable_cluster_creator_admin_permissions = true

  # Controllers will use EKS Pod Identity rather than IRSA.
  enable_irsa = false

  # EKS 1.28+ encrypts API data with an AWS-owned key by default.
  create_kms_key    = false
  encryption_config = null

  enabled_log_types                      = ["api", "audit", "authenticator"]
  cloudwatch_log_group_retention_in_days = 7

  addons = {
    vpc-cni = {
      addon_version  = "v1.22.4-eksbuild.10"
      before_compute = true
    }
    eks-pod-identity-agent = {
      addon_version  = "v1.4.1-eksbuild.3"
      before_compute = true
    }
    kube-proxy = {
      addon_version = "v1.36.0-eksbuild.58"
    }
    coredns = {
      addon_version = "v1.14.7-eksbuild.22"
    }
  }

  eks_managed_node_groups = {
    general = {
      ami_type                       = "AL2023_x86_64_STANDARD"
      ami_release_version            = "1.36.5-20261031"
      use_latest_ami_release_version = false
      instance_types                 = ["t3.small"]
      capacity_type                  = "ON_DEMAND"

      min_size     = 2
      max_size     = 2
      desired_size = 2

      # Standard mode throttles CPU after credits run out rather than
      # charging for surplus CPU credits. Suitable for this small lab.
      credit_specification = {
        cpu_credits = "standard"
      }

      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 1
      }

      block_device_mappings = {
        root = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 20
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      update_config = {
        max_unavailable = 1
      }

      # Bootstrap networking permissions for the aws-node DaemonSet.
      iam_role_attach_cni_policy = true
    }
  }
}

output "eks_cluster_name" {
  description = "Cluster name used by update-kubeconfig."
  value       = module.eks.cluster_name
}

output "eks_node_security_group_id" {
  description = "Worker security group, used later when allowing access to RDS."
  value       = module.eks.node_security_group_id
}
