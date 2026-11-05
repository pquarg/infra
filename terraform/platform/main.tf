resource "kubernetes_manifest" "traefik" {
  # Terraform owns the registration; Argo CD owns the chart's resources.
  manifest = yamldecode(file("${path.module}/../../argocd/traefik.yaml"))

  field_manager {
    name            = "infra-local-terraform"
    force_conflicts = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "context" {
  value = "kind-${var.cluster_name}"
}
