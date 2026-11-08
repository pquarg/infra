resource "helm_release" "load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = trimspace(file("${path.module}/../../load-balancer-controller/chart-version.txt"))
  namespace  = "kube-system"

  atomic        = true
  wait          = true
  wait_for_jobs = true
  timeout       = 600
  max_history   = 5

  values = [
    file("${path.module}/../../load-balancer-controller/values.yaml"),
    yamlencode({
      clusterName = var.cluster_name
      region      = "eu-north-1"
      vpcId       = data.aws_eks_cluster.selected.vpc_config[0].vpc_id
    }),
  ]
  take_ownership = false
  force_update   = false
}
