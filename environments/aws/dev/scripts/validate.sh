#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
for tool in terraform python3 node helm kubeconform kubectl; do
  command -v "$tool" > /dev/null || { printf 'Error: %s is required.\n' "$tool" >&2; exit 1; }
done
repository="$(cd "$root/../../.." && pwd)"
terraform fmt -check -recursive "$repository/bootstrap/aws" foundation platform edge
for script in scripts/*.sh tests/*.sh; do bash -n "$script"; done
bash tests/stages.sh
node --test tests/*.test.cjs
python3 -m unittest discover -s tests -p 'test_*.py'
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
python3 "$repository/scripts/update-gateway-schemas.py" --output "$tmp/schemas"
diff -r "$repository/shared/gateway-schemas" "$tmp/schemas"
# Mirror the repository layout so every relative asset is checked without private files.
validation_root="$tmp/environments/aws/dev"
mkdir -p "$validation_root" "$tmp/shared"
cp -R "$repository/shared/." "$tmp/shared/"
for directory in functions policies argocd load-balancer-controller; do cp -R "$directory" "$validation_root/"; done
for stage in bootstrap/aws environments/aws/dev/foundation environments/aws/dev/platform environments/aws/dev/edge; do
  mkdir -p "$tmp/$stage"
  cp "$repository/$stage"/*.tf "$repository/$stage/.terraform.lock.hcl" "$tmp/$stage/"
  terraform -chdir="$tmp/$stage" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="$tmp/$stage" validate
done
for chart in gateway-api-crds aws-controller-crds alb-gateway-class; do
  if [[ -f "$repository/shared/$chart/SHA256SUMS" ]]; then
    (cd "$repository/shared/$chart" && shasum -a 256 -c SHA256SUMS)
  fi
  helm lint "$repository/shared/$chart"
  helm template "$chart" "$repository/shared/$chart" > "$tmp/$chart.yaml"
  kubeconform -strict -summary -skip CustomResourceDefinition \
    -schema-location default \
    -schema-location "$repository/shared/gateway-schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" "$tmp/$chart.yaml"
done
helm template aws-load-balancer-controller aws-load-balancer-controller \
  --repo https://aws.github.io/eks-charts --version "$(cat load-balancer-controller/chart-version.txt)" \
  --namespace kube-system --values load-balancer-controller/values.yaml \
  --set clusterName=deployment-project-dev > "$tmp/controller.yaml"
kubeconform -strict -summary -schema-location default \
  -schema-location "$repository/shared/gateway-schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" "$tmp/controller.yaml"
python3 "$repository/tests/gateway-platform.py" aws "$tmp/gateway-api-crds.yaml" "$tmp/aws-controller-crds.yaml" "$tmp/alb-gateway-class.yaml"
printf 'AWS configuration checks passed; no infrastructure was deployed.\n'
