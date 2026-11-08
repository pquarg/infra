resource "aws_iam_policy" "load_balancer_controller" {
  name        = "deployment-project-dev-load-balancer-controller"
  description = "Official AWS Load Balancer Controller v3.6.2 permissions"
  policy      = file("${path.module}/../../policies/aws-load-balancer-controller-v3.6.2.json")
}

resource "aws_iam_role" "load_balancer_controller" {
  name = "deployment-project-dev-load-balancer-controller"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowControllerPodIdentity"
      Effect = "Allow"
      Principal = {
        Service = "pods.eks.amazonaws.com"
      }
      Action = ["sts:AssumeRole", "sts:TagSession"]
      Condition = {
        StringEquals = {
          "aws:RequestTag/eks-cluster-arn"            = module.eks.cluster_arn
          "aws:RequestTag/kubernetes-namespace"       = "kube-system"
          "aws:RequestTag/kubernetes-service-account" = "aws-load-balancer-controller"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "load_balancer_controller" {
  role       = aws_iam_role.load_balancer_controller.name
  policy_arn = aws_iam_policy.load_balancer_controller.arn
}

resource "aws_eks_pod_identity_association" "load_balancer_controller" {
  cluster_name         = module.eks.cluster_name
  namespace            = "kube-system"
  service_account      = "aws-load-balancer-controller"
  role_arn             = aws_iam_role.load_balancer_controller.arn
  disable_session_tags = false
}

output "load_balancer_controller_role_arn" {
  description = "Dedicated Pod Identity role for the load balancer controller."
  value       = aws_iam_role.load_balancer_controller.arn
}
