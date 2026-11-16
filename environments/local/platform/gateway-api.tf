resource "helm_release" "gateway_api_crds" {
  name             = "gateway-api-crds"
  chart            = "${path.module}/../../../shared/gateway-api-crds"
  version          = yamldecode(file("${path.module}/../../../shared/gateway-api-crds/Chart.yaml")).version
  namespace        = "gateway-system"
  create_namespace = true
  atomic           = true
  wait             = true
  timeout          = 600
  max_history      = 5
  take_ownership   = false
  force_update     = false

  lifecycle {
    prevent_destroy = true
  }
}
