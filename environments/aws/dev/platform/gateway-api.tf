resource "helm_release" "gateway_api_crds" {
  name             = "gateway-api-crds"
  chart            = "${path.module}/../../../../shared/gateway-api-crds"
  version          = yamldecode(file("${path.module}/../../../../shared/gateway-api-crds/Chart.yaml")).version
  namespace        = "gateway-system"
  create_namespace = true
  atomic           = true
  wait             = true
  timeout          = 600
  max_history      = 5
  take_ownership   = false
  force_update     = false
}

resource "helm_release" "aws_controller_crds" {
  name             = "aws-controller-crds"
  chart            = "${path.module}/../../../../shared/aws-controller-crds"
  version          = yamldecode(file("${path.module}/../../../../shared/aws-controller-crds/Chart.yaml")).version
  namespace        = "kube-system"
  create_namespace = true
  atomic           = true
  wait             = true
  timeout          = 600
  max_history      = 5
  take_ownership   = false
  force_update     = false
}

resource "helm_release" "alb_gateway_class" {
  name             = "alb-gateway-class"
  chart            = "${path.module}/../../../../shared/alb-gateway-class"
  version          = yamldecode(file("${path.module}/../../../../shared/alb-gateway-class/Chart.yaml")).version
  namespace        = "kube-system"
  create_namespace = true
  atomic           = true
  wait             = true
  timeout          = 600
  max_history      = 5
  take_ownership   = false
  force_update     = false
  depends_on       = [helm_release.gateway_api_crds, helm_release.aws_controller_crds, helm_release.load_balancer_controller]
}
