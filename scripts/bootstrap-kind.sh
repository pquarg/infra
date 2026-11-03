#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [cluster]\n' "${0##*/}"
  printf 'Default: cluster=argocd-postgres; requires an existing kind cluster.\n'
  printf 'Installs pinned Argo CD and registers shared Traefik from this checkout.\n'
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
for required_command in kubectl kind docker; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required; see README.md.\n' "$required_command" >&2
    exit 1
  fi
done
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cluster="${1:-argocd-postgres}"
kube=(kubectl "--context=kind-${cluster}")
# Check the named cluster exists before applying anything; never use the ambient context.
kind get nodes --name "$cluster" > /dev/null
"${kube[@]}" get nodes > /dev/null
"${kube[@]}" apply --server-side -k "$project_root/argocd/install"
"${kube[@]}" wait --for=condition=Established \
  crd/applications.argoproj.io crd/applicationsets.argoproj.io --timeout=120s
"${kube[@]}" -n argocd rollout status deployment/argocd-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-repo-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-applicationset-controller --timeout=300s
"${kube[@]}" -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
"${kube[@]}" apply -k "$project_root/argocd"
"$project_root/scripts/setup-ingress-kind.sh" "$cluster"
printf 'Platform ready. Follow README.md to connect deploy-local and provision app credentials.\n'
