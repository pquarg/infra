#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [cluster]\n' "${0##*/}"
  printf 'Default: cluster=deployment; requires an existing kind cluster.\n'
  printf 'Runs one reviewed Terraform apply: pinned Argo CD and Traefik Helm releases.\n'
  printf 'For an existing installation, first follow docs/terraform.md#adopt-an-existing-installation.\n'
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
# Check the named cluster exists before applying anything; never use the ambient context.
kind get nodes --name "$cluster" > /dev/null
"${kube[@]}" get nodes > /dev/null
terraform_args=("-var=cluster_name=$cluster" "-var=kubeconfig_path=$kubeconfig")
# Inspect both states before initializing; legacy ownership must be transferred explicitly.
tracked_resources=""
for stage in argocd platform; do
  stage_state="$project_root/terraform/$stage/terraform.tfstate"
  if [[ -f "$stage_state" ]]; then
    tracked_context="$(terraform -chdir="$project_root" output "-state=$stage_state" -raw context 2>/dev/null || true)"
    if [[ -n "$tracked_context" && "$tracked_context" != "kind-$cluster" ]]; then
      printf 'Error: %s state belongs to %s. Use separate state/checkouts for different clusters.\n' "$stage" "$tracked_context" >&2
      exit 1
    fi
    resources="$(terraform -chdir="$project_root" state list "-state=$stage_state")"
    if [[ -n "$resources" && ( "$stage" == argocd || "$resources" == *'kubernetes_'* || "$resources" == *'kubectl_'* ) ]]; then
      printf 'Error: legacy ownership needs Helm migration into terraform/platform; see docs/terraform.md#adopt-an-existing-installation.\n' >&2
      exit 1
    fi
    if [[ "$stage" == platform ]]; then tracked_resources="$resources"; fi
  fi
done
# Refuse all existing unadopted installations before the single apply.
existing_application=""
application_crd="$("${kube[@]}" get crd applications.argoproj.io --ignore-not-found -o name)"
if [[ -n "$application_crd" ]]; then
  existing_application="$("${kube[@]}" -n argocd get application traefik --ignore-not-found -o name)"
fi
if [[ -n "$existing_application" ]]; then
  printf 'Error: detach the Traefik Application without cascading deletion before Helm adoption; see docs/terraform.md.\n' >&2
  exit 1
fi
for release in argocd traefik; do
  if ! grep -Fxq "helm_release.$release" <<< "$tracked_resources"; then
    deployment="$release"
    if [[ "$release" == argocd ]]; then deployment=argocd-server; fi
    existing="$("${kube[@]}" -n "$release" get deployment "$deployment" --ignore-not-found -o name)"
    # Also detect a real Helm record even if its Deployment is absent.
    records="$("${kube[@]}" -n "$release" get secrets -l "owner=helm,name=$release" -o name)"
    if [[ -n "$existing" || -n "$records" ]]; then
      printf 'Error: existing %s is untracked. Adopt/import it first; see docs/terraform.md.\n' "$release" >&2
      exit 1
    fi
  fi
done
terraform -chdir="$project_root/terraform/platform" init -input=false -lockfile=readonly
printf 'Review the combined Argo CD and Traefik Helm plan.\n'
terraform -chdir="$project_root/terraform/platform" apply "${terraform_args[@]}"
"${kube[@]}" wait --for=condition=Established \
  crd/applications.argoproj.io crd/applicationsets.argoproj.io --timeout=120s
"${kube[@]}" -n argocd rollout status deployment/argocd-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-repo-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-applicationset-controller --timeout=300s
"${kube[@]}" -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
"$project_root/scripts/setup-ingress-kind.sh" "$cluster"
printf 'Platform ready. Follow README.md to connect gitops and provision app credentials.\n'
