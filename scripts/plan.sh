#!/usr/bin/env bash
# A full plan for exactly one target/stage. Apply saved plans separately after review.
set -euo pipefail
repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repository/scripts/targets.sh"
environment=""
stage=""
context=""
kubeconfig="${KUBECONFIG:-$HOME/.kube/config}"
while (( $# )); do
  case "$1" in
    --environment|--stage|--context|--kubeconfig)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || exit 1
      case "$1" in
        --environment) [[ -z "$environment" ]] || exit 1; environment="$2" ;;
        --stage) [[ -z "$stage" ]] || exit 1; stage="$2" ;;
        --context) [[ -z "$context" ]] || exit 1; context="$2" ;;
        --kubeconfig) kubeconfig="$2" ;;
      esac
      shift 2 ;;
    --help|-h)
      printf 'Usage: %s --environment local|aws/dev --stage platform|foundation|edge [--context CONTEXT] [--kubeconfig PATH]\n' "${0##*/}"
      printf 'Local supports platform only. AWS platform/edge and local require an explicit context. Bootstrap is separately operated.\n'; exit 0 ;;
    *) printf 'Error: unknown option %s.\n' "$1" >&2; exit 1 ;;
  esac
done
select_target
case "$environment:$stage" in
  local:platform) [[ "$context" == kind-deployment ]] || { printf 'Error: local platform requires --context kind-deployment.\n' >&2; exit 1; } ;;
  aws/dev:foundation) ;;
  aws/dev:platform|aws/dev:edge) [[ -n "$context" && "$context" != kind-* ]] || { printf 'Error: select an explicit AWS context.\n' >&2; exit 1; } ;;
  *) printf 'Error: invalid stage for %s; bootstrap is separate.\n' "$environment" >&2; exit 1 ;;
esac
if [[ "$environment:$stage" != aws/dev:foundation ]]; then
  [[ "$kubeconfig" == /* && "$kubeconfig" != *:* ]] || { printf 'Error: select one absolute kubeconfig file.\n' >&2; exit 1; }
fi
# Keep ambient Terraform arguments/workspaces from redirecting state or backend selection.
for name in ${!TF_CLI_ARGS*}; do unset "$name"; done
unset TF_DATA_DIR TF_LOG TF_LOG_PATH
export TF_WORKSPACE=default
umask 077
printf 'Target: %s; stage: %s; root: %s\n' "$environment" "$stage" "$target_root/$stage"
if [[ "$environment" == aws/dev ]]; then
  printf 'AWS account: 245361883993; profile: aws-project; region: eu-north-1; cluster: deployment-project-dev\n'
  case "$stage" in foundation) key=dev/terraform.tfstate ;; *) key="dev/$stage/terraform.tfstate" ;; esac
  printf 'Backend: deployment-project-tfstate-245361883993-eu-north-1/%s\n' "$key"
  aws_options=(--kubeconfig "$kubeconfig")
  if [[ -n "$context" ]]; then aws_options+=(--context "$context"); fi
  exec "$target_root/scripts/plan.sh" "$stage" "${aws_options[@]}"
fi
printf 'Local state: %s/platform/terraform.tfstate; context: %s; kubeconfig: %s\n' "$target_root" "$context" "$kubeconfig"
"$target_root/scripts/check-platform.sh" deployment "$kubeconfig"
terraform -chdir="$target_root/platform" init -input=false -lockfile=readonly
terraform -chdir="$target_root/platform" plan -input=false -lock-timeout=5m \
  -var=cluster_name=deployment "-var=kubeconfig_path=$kubeconfig" -out=platform.tfplan
terraform -chdir="$target_root/platform" show platform.tfplan
printf 'Review the saved plan, then apply with: terraform -chdir=%q apply platform.tfplan\n' "$target_root/platform"
