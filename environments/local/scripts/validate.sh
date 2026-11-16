#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
for required_command in kubectl kubeconform terraform helm docker; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required; see README.md.\n' "$required_command" >&2
    exit 1
  fi
done
bash tests/bootstrap.sh
bash tests/teardown.sh
bash tests/database.sh
python3 -m unittest discover -s tests -p "test_*.py"
terraform fmt -check -recursive platform
for script in scripts/*.sh tests/*.sh database/*.sh; do
  bash -n "$script"
done
render_dir="$(mktemp -d)"
trap 'rm -rf -- "$render_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 "$project_root/../../scripts/update-gateway-schemas.py" --output "$render_dir/schemas"
diff -r "$project_root/../../shared/gateway-schemas" "$render_dir/schemas"
# Configuration only; no Docker daemon, real settings, passwords or volumes.
LOCAL_DB_SECRETS_DIR="$render_dir/secrets" LOCAL_DB_PORT=5433 \
  docker compose --env-file database/.env.example -f database/compose.yaml config --quiet
# Validate clean copies so working state/providers and local .tfvars are untouched.
validation_root="$render_dir/environments/local"
mkdir -p "$validation_root/argocd" "$validation_root/traefik" "$render_dir/shared/argocd"
cp -R "$project_root/../../shared/." "$render_dir/shared/"
cp argocd/chart-version.txt argocd/values.yaml "$validation_root/argocd/"
cp traefik/chart-version.txt traefik/values.yaml "$validation_root/traefik/"
for stage in platform; do
  stage_root="$validation_root/$stage"
  mkdir -p "$stage_root"
  cp "$stage"/*.tf "$stage"/.terraform.lock.hcl "$stage_root/"
  terraform -chdir="$stage_root" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="$stage_root" validate
done
helm template argocd argo-cd \
  --repo https://argoproj.github.io/argo-helm \
  --version "$(cat argocd/chart-version.txt)" \
  --namespace argocd --kube-version "${KUBERNETES_VERSION:-1.36.0}" \
  --values "$project_root/../../shared/argocd/values.yaml" --values argocd/values.yaml --include-crds > "$render_dir/argocd-install.yaml"
helm pull traefik --repo https://traefik.github.io/charts \
  --version "$(cat traefik/chart-version.txt)" --destination "$render_dir"
helm template traefik "$render_dir/traefik-$(cat traefik/chart-version.txt).tgz" \
  --namespace traefik \
  --kube-version "${KUBERNETES_VERSION:-1.36.0}" --skip-crds \
  --values traefik/values.yaml > "$render_dir/platform.yaml"
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
  -schema-location "$project_root/../../shared/gateway-schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  "$render_dir/platform.yaml"
for chart in gateway-api-crds; do
  (cd "$project_root/../../shared/$chart" && shasum -a 256 -c SHA256SUMS)
  helm lint "$project_root/../../shared/$chart"
  helm template "$chart" "$project_root/../../shared/$chart" > "$render_dir/$chart.yaml"
  kubeconform -strict -summary -skip CustomResourceDefinition "$render_dir/$chart.yaml"
done
python3 "$project_root/../../tests/gateway-platform.py" local "$render_dir/platform.yaml" "$render_dir/gateway-api-crds.yaml"
printf 'Platform configuration checks passed.\n'
