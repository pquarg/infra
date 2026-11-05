#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [cluster]\n' "${0##*/}"
  printf 'Default: cluster=argocd-postgres; requires an existing kind cluster.\n'
  printf 'Runs two reviewed Terraform applies: pinned Argo CD, then shared Traefik registration.\n'
  printf 'For an existing installation, first run python3 scripts/import-platform.py [cluster].\n'
  printf 'Does not register or deploy apps from deploy-local.\n'
}
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 1
fi
for required_command in kubectl kind docker terraform python3; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required; see README.md.\n' "$required_command" >&2
    exit 1
  fi
done
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cluster="${1:-argocd-postgres}"
if [[ ! "$cluster" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  printf 'Error: invalid kind cluster name.\n' >&2
  exit 1
fi
# Match Terraform's explicit kubeconfig instead of the ambient KUBECONFIG list.
kubeconfig="${INFRA_LOCAL_KUBECONFIG:-$HOME/.kube/config}"
kube=(kubectl "--kubeconfig=$kubeconfig" "--context=kind-${cluster}")
umask 077
# Check the named cluster exists before applying anything; never use the ambient context.
kind get nodes --name "$cluster" > /dev/null
"${kube[@]}" get nodes > /dev/null
python3 "$project_root/scripts/render-platform.py"
terraform_args=("-var=cluster_name=$cluster" "-var=kubeconfig_path=$kubeconfig")
for stage in argocd platform; do
  stage_root="$project_root/terraform/$stage"
  terraform -chdir="$stage_root" init -input=false
  if [[ -f "$stage_root/terraform.tfstate" ]]; then
    tracked_context="$(terraform -chdir="$stage_root" output -raw context 2>/dev/null || true)"
    if [[ -n "$tracked_context" && "$tracked_context" != "kind-$cluster" ]]; then
      printf 'Error: %s state belongs to %s. Use separate state/checkouts for different clusters.\n' "$stage" "$tracked_context" >&2
      exit 1
    fi
    tracked_resources="$(terraform -chdir="$stage_root" state list)"
    if [[ "$tracked_resources" == *'kubernetes_secret_v1.installation['* ]]; then
      printf 'Error: Secret state migration required; see docs/terraform.md#secret-state-migration.\n' >&2
      exit 1
    fi
  fi
done
printf 'Review the Argo CD plan. Existing resources must be imported before this apply.\n'
terraform -chdir="$project_root/terraform/argocd" apply "${terraform_args[@]}"
"${kube[@]}" wait --for=condition=Established \
  crd/applications.argoproj.io crd/applicationsets.argoproj.io --timeout=120s
"${kube[@]}" -n argocd rollout status deployment/argocd-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-repo-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-applicationset-controller --timeout=300s
"${kube[@]}" -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
printf 'Argo CD is ready. Review the separate Traefik registration plan.\n'
terraform -chdir="$project_root/terraform/platform" apply "${terraform_args[@]}"
"$project_root/scripts/setup-ingress-kind.sh" "$cluster"
printf 'Platform ready. Follow README.md to connect deploy-local and provision app credentials.\n'
