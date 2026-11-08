#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
for tool in terraform python3 node; do
  command -v "$tool" > /dev/null || { printf 'Error: %s is required.\n' "$tool" >&2; exit 1; }
done
terraform fmt -check -recursive bootstrap terraform
for script in scripts/*.sh tests/*.sh; do bash -n "$script"; done
bash tests/stages.sh
node --test tests/*.test.cjs
python3 -m unittest discover -s tests -p 'test_*.py'
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
# Copy only checked-in configuration, never state, backend caches or local variables.
for directory in functions policies argocd load-balancer-controller; do cp -R "$directory" "$tmp/"; done
for stage in bootstrap terraform/foundation terraform/platform terraform/edge; do
  mkdir -p "$tmp/$stage"
  cp "$stage"/*.tf "$stage/.terraform.lock.hcl" "$tmp/$stage/"
  terraform -chdir="$tmp/$stage" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="$tmp/$stage" validate
done
printf 'AWS configuration checks passed; no infrastructure was deployed.\n'
