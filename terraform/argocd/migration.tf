# Retire individual manifest bindings without deleting Kubernetes resources.
# Leave these blocks in place for checkouts adopting the Helm installation.
# Helm adoption/import is a separate, reviewed procedure in docs/terraform.md.
removed {
  from = kubernetes_manifest.namespace

  lifecycle {
    destroy = false
  }
}

removed {
  from = kubernetes_manifest.installation

  lifecycle {
    destroy = false
  }
}

removed {
  from = kubectl_manifest.installation

  lifecycle {
    destroy = false
  }
}

removed {
  from = kubernetes_secret_v1.installation

  lifecycle {
    destroy = false
  }
}
