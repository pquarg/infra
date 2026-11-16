#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [cluster]\n' "${0##*/}"
  printf 'Default: cluster=deployment; requires an existing kind cluster.\n'
  printf 'Runs one reviewed Terraform apply: pinned Standard CRDs, Argo CD and Traefik Helm releases.\n'
  printf 'Does not register or deploy apps from gitops.\n'
}
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 1
fi
for required_command in kubectl kind docker terraform; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required; see README.md.\n' "$required_command" >&2
    exit 1
  fi
done
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cluster="${1:-deployment}"
if [[ ! "$cluster" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  printf 'Error: invalid kind cluster name.\n' >&2
  exit 1
fi
# Match Terraform's explicit kubeconfig instead of the ambient KUBECONFIG list.
kubeconfig="${INFRA_LOCAL_KUBECONFIG:-$HOME/.kube/config}"
kube=(kubectl "--kubeconfig=$kubeconfig" "--context=kind-${cluster}")
umask 077
"$project_root/scripts/check-platform.sh" "$cluster" "$kubeconfig"
terraform_args=("-var=cluster_name=$cluster" "-var=kubeconfig_path=$kubeconfig")
terraform -chdir="$project_root/platform" init -input=false -lockfile=readonly
printf 'Review the combined CRD, Argo CD and Traefik Helm plan.\n'
terraform -chdir="$project_root/platform" apply "${terraform_args[@]}"
"${kube[@]}" wait --for=condition=Established \
  crd/applications.argoproj.io crd/applicationsets.argoproj.io --timeout=120s
"${kube[@]}" -n argocd rollout status deployment/argocd-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-repo-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-applicationset-controller --timeout=300s
"${kube[@]}" -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
"$project_root/scripts/check-ingress-kind.sh" "$cluster"
printf 'Platform ready. Follow README.md to connect gitops and provision app credentials.\n'
