locals {
  # Render the pinned Kustomization with scripts/render-platform.py first.
  # Stable resource identities, rather than list positions, survive reordering.
  installation = jsondecode(file("${path.module}/../../.generated/argocd-install.json"))
}

resource "kubernetes_manifest" "namespace" {
  manifest = yamldecode(file("${path.module}/../../argocd/install/namespace.yaml"))

  field_manager {
    name = "infra-local-terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "kubernetes_manifest" "installation" {
  for_each = { for key, manifest in local.installation : key => manifest if manifest.kind != "Secret" }
  manifest = each.value

  # Retire the former kubectl installer when adopting these same objects.
  # Take ownership only of fields declared in the pinned installation.
  field_manager {
    name            = "infra-local-terraform"
    force_conflicts = true
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [kubernetes_manifest.namespace]
}

resource "kubernetes_secret_v1" "installation" {
  # Use the typed resource so controller-populated credentials stay sensitive
  # in plans. Terraform creates the empty upstream Secret, not its credentials.
  for_each = { for key, manifest in local.installation : key => manifest if manifest.kind == "Secret" }

  metadata {
    name      = each.value.metadata.name
    namespace = each.value.metadata.namespace
    labels    = try(each.value.metadata.labels, {})
  }
  type = try(each.value.type, "Opaque")

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [data, binary_data]
  }

  depends_on = [kubernetes_manifest.namespace]
}

output "context" {
  value = "kind-${var.cluster_name}"
}
