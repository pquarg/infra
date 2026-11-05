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

resource "kubectl_manifest" "installation" {
  # The typed Kubernetes Secret resource hangs formatting metadata on this host.
  # This provider redacts credentials without using that typed-resource path.
  # Own only the empty upstream Secret; Argo CD owns its populated data.
  for_each  = { for key, manifest in local.installation : key => manifest if manifest.kind == "Secret" }
  yaml_body = jsonencode(each.value)

  server_side_apply = true
  field_manager     = "infra-local-terraform"
  force_conflicts   = true
  ignore_fields     = ["data", "stringData"]
  sensitive_fields  = ["data", "stringData"]

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [kubernetes_manifest.namespace]
}

output "context" {
  value = "kind-${var.cluster_name}"
}
