resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = trimspace(file("${path.module}/../../argocd/chart-version.txt"))
  namespace        = "argocd"
  create_namespace = true

  atomic        = true
  wait          = true
  wait_for_jobs = true
  timeout       = 600
  max_history   = 5

  values = [file("${path.module}/../../argocd/values.yaml")]

  # Existing manifest installations need the reviewed Helm adoption procedure;
  # routine Terraform applies must never take ownership or force replacement.
  take_ownership = false
  force_update   = false

  lifecycle {
    prevent_destroy = true
  }
}

output "context" {
  value = "kind-${var.cluster_name}"
}
