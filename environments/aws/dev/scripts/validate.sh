#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
for tool in terraform python3 node; do
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
# Mirror the repository layout so every relative asset is checked without private files.
validation_root="$tmp/environments/aws/dev"
mkdir -p "$validation_root" "$tmp/shared"
cp -R "$repository/shared/argocd" "$tmp/shared/"
for directory in functions policies argocd load-balancer-controller; do cp -R "$directory" "$validation_root/"; done
for stage in bootstrap/aws environments/aws/dev/foundation environments/aws/dev/platform environments/aws/dev/edge; do
  mkdir -p "$tmp/$stage"
  cp "$repository/$stage"/*.tf "$repository/$stage/.terraform.lock.hcl" "$tmp/$stage/"
  terraform -chdir="$tmp/$stage" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="$tmp/$stage" validate
done
printf 'AWS configuration checks passed; no infrastructure was deployed.\n'
