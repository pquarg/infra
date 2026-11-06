#!/usr/bin/env bash
# Save a full stage plan for review. Applying it is a separate operator action.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
stage="${1:-}"
case "$stage" in foundation|platform|edge) shift ;; *) printf 'Usage: %s foundation|platform|edge [--context CONTEXT] [--kubeconfig PATH]\n' "${0##*/}" >&2; exit 1 ;; esac
"$root/scripts/check-stage.sh" "$stage" "$@"
umask 077
stage_root="$root/terraform/$stage"
terraform -chdir="$stage_root" init -input=false -lockfile=readonly
# The retained foundation key may still contain the old monolithic owners.
# Refuse to plan until the explicit transfer runbook has dealt with each one.
if [[ "$stage" != foundation ]]; then
  terraform -chdir="$root/terraform/foundation" init -input=false -lockfile=readonly
fi
bindings="$(terraform -chdir="$root/terraform/foundation" state list)"
if grep -Eq '^(helm_release\.|aws_cloudfront_|aws_cloudwatch_log_delivery|aws_s3_bucket[^.]*\.cloudfront_logs)' <<< "$bindings"; then
  printf 'Error: legacy platform/edge bindings remain in foundation. Follow docs/state-migration.md before any apply.\n' >&2
  exit 1
fi

args=(-input=false -lock-timeout=5m)
if [[ "$stage" == foundation || "$stage" == edge ]]; then
  config="$root/config/operator.tfvars"
  [[ -f "$config" ]] || { printf 'Error: prepare ignored config/operator.tfvars; see README.md.\n' >&2; exit 1; }
  args+=("-var-file=$config")
fi
terraform -chdir="$stage_root" plan "${args[@]}" -out=stage.tfplan
terraform -chdir="$stage_root" show stage.tfplan
printf 'Review the saved plan, then apply with: terraform -chdir=%q apply stage.tfplan\n' "$stage_root"
