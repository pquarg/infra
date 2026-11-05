#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
for required_command in kubectl kubeconform terraform python3; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required; see README.md.\n' "$required_command" >&2
    exit 1
  fi
done
python3 -B -m unittest discover -s tests
python3 scripts/render-platform.py
terraform fmt -check -recursive terraform
for stage in argocd platform; do
  terraform -chdir="terraform/$stage" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="terraform/$stage" validate
done
for script in scripts/*.sh; do
  bash -n "$script"
done
render_dir="$(mktemp -d)"
trap 'rm -rf -- "$render_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
kubectl kustomize argocd/install > "$render_dir/argocd-install.yaml"
kubectl kustomize argocd > "$render_dir/platform.yaml"
# The schema registry has no CustomResourceDefinition schema. These three
# definitions come unchanged from the pinned upstream Argo CD install. Skip
# only those definitions, not unknown custom resources in our platform config.
kubeconform -strict -summary -skip CustomResourceDefinition \
  -kubernetes-version "${KUBERNETES_VERSION:-1.36.0}" \
  -schema-location default \
  "$render_dir/argocd-install.yaml"
kubeconform -strict -summary \
  -kubernetes-version "${KUBERNETES_VERSION:-1.36.0}" \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  "$render_dir/platform.yaml"
printf 'Platform configuration checks passed.\n'
