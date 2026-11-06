#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
for required_command in kubectl kubeconform terraform helm; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required; see README.md.\n' "$required_command" >&2
    exit 1
  fi
done
bash tests/bootstrap.sh
terraform fmt -check -recursive terraform
for script in scripts/*.sh tests/*.sh; do
  bash -n "$script"
done
render_dir="$(mktemp -d)"
trap 'rm -rf -- "$render_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Validate clean copies so legacy state/providers and local .tfvars are untouched.
mkdir -p "$render_dir/argocd"
cp argocd/chart-version.txt argocd/values.yaml argocd/traefik.yaml "$render_dir/argocd/"
for stage in argocd platform; do
  stage_root="$render_dir/terraform/$stage"
  mkdir -p "$stage_root"
  cp terraform/"$stage"/*.tf terraform/"$stage"/.terraform.lock.hcl "$stage_root/"
  terraform -chdir="$stage_root" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="$stage_root" validate
done
helm template argocd argo-cd \
  --repo https://argoproj.github.io/argo-helm \
  --version "$(cat argocd/chart-version.txt)" \
  --namespace argocd --kube-version "${KUBERNETES_VERSION:-1.36.0}" \
  --values argocd/values.yaml --include-crds > "$render_dir/argocd-install.yaml"
kubectl kustomize argocd > "$render_dir/platform.yaml"
# The schema registry has no CustomResourceDefinition schema. These three
# definitions come from the pinned Argo CD chart. Skip
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
