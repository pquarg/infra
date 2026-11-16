resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = trimspace(file("${path.module}/../argocd/chart-version.txt"))
  namespace        = "argocd"
  create_namespace = true

  atomic        = true
  wait          = true
  wait_for_jobs = true
  timeout       = 600
  max_history   = 5

  values = [file("${path.module}/../../../shared/argocd/values.yaml"), file("${path.module}/../argocd/values.yaml")]

  # Keep resource ownership explicit and avoid forced replacements.
  take_ownership = false
  force_update   = false

  lifecycle {
    prevent_destroy = true
  }
}

output "context" {
  value = "kind-${var.cluster_name}"
}

resource "helm_release" "traefik" {
  depends_on       = [helm_release.gateway_api_crds]
  name             = "traefik"
  repository       = "https://traefik.github.io/charts"
  chart            = "traefik"
  version          = trimspace(file("${path.module}/../traefik/chart-version.txt"))
  namespace        = "traefik"
  create_namespace = true
  skip_crds        = true

  atomic        = true
  wait          = true
  wait_for_jobs = true
  timeout       = 600
  max_history   = 5
  values        = [file("${path.module}/../traefik/values.yaml")]

  take_ownership = false
  force_update   = false

  lifecycle {
    prevent_destroy = true
  }
}
