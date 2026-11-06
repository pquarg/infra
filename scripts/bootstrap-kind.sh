#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [cluster]\n' "${0##*/}"
  printf 'Default: cluster=argocd-postgres; requires an existing kind cluster.\n'
  printf 'Runs two reviewed Terraform applies: pinned Argo CD, then shared Traefik registration.\n'
  printf 'For an existing installation, first follow docs/terraform.md#adopt-an-existing-installation.\n'
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
for required_command in kubectl kind docker terraform; do
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
terraform_args=("-var=cluster_name=$cluster" "-var=kubeconfig_path=$kubeconfig")
argocd_tracked=false
platform_tracked=false
for stage in argocd platform; do
  stage_root="$project_root/terraform/$stage"
  stage_state="$stage_root/terraform.tfstate"
  if [[ -f "$stage_state" ]]; then
    # Read explicit local state from the configuration-free repository root.
    # This detects legacy ownership before init downloads obsolete providers.
    tracked_context="$(terraform -chdir="$project_root" output "-state=$stage_state" -raw context 2>/dev/null || true)"
    if [[ -n "$tracked_context" && "$tracked_context" != "kind-$cluster" ]]; then
      printf 'Error: %s state belongs to %s. Use separate state/checkouts for different clusters.\n' "$stage" "$tracked_context" >&2
      exit 1
    fi
    tracked_resources="$(terraform -chdir="$project_root" state list "-state=$stage_state")"
    if [[ "$stage" == argocd && ( "$tracked_resources" == *'kubernetes_manifest.'* ||
      "$tracked_resources" == *'kubectl_manifest.'* || "$tracked_resources" == *'kubernetes_secret_v1.'* ) ]]; then
      printf 'Error: manifest-based Argo CD state needs Helm migration; see docs/terraform.md#adopt-an-existing-installation.\n' >&2
      exit 1
    fi
    if [[ "$stage" == argocd && "$tracked_resources" == *'helm_release.argocd'* ]]; then
      argocd_tracked=true
    fi
    if [[ "$stage" == platform && "$tracked_resources" == *'kubernetes_manifest.traefik'* ]]; then
      platform_tracked=true
    fi
  fi
done
if [[ "$argocd_tracked" != true ]]; then
  existing_server="$("${kube[@]}" -n argocd get deployment argocd-server --ignore-not-found -o name)"
  if [[ -n "$existing_server" ]]; then
    printf 'Error: existing Argo CD is not tracked as a Helm release. Follow docs/terraform.md#adopt-an-existing-installation before applying.\n' >&2
    exit 1
  fi
fi
for stage in argocd platform; do
  terraform -chdir="$project_root/terraform/$stage" init -input=false -lockfile=readonly
done
printf 'Review the Argo CD Helm plan. Existing installations must be adopted before this apply.\n'
terraform -chdir="$project_root/terraform/argocd" apply "${terraform_args[@]}"
"${kube[@]}" wait --for=condition=Established \
  crd/applications.argoproj.io crd/applicationsets.argoproj.io --timeout=120s
"${kube[@]}" -n argocd rollout status deployment/argocd-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-repo-server --timeout=300s
"${kube[@]}" -n argocd rollout status deployment/argocd-applicationset-controller --timeout=300s
"${kube[@]}" -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
if [[ "$platform_tracked" != true ]]; then
  existing_traefik="$("${kube[@]}" -n argocd get application traefik --ignore-not-found -o name)"
  if [[ -n "$existing_traefik" ]]; then
    printf 'Error: import the existing Traefik Application first; see docs/terraform.md#adopt-an-existing-installation.\n' >&2
    exit 1
  fi
fi
printf 'Argo CD is ready. Review the separate Traefik registration plan.\n'
terraform -chdir="$project_root/terraform/platform" apply "${terraform_args[@]}"
"$project_root/scripts/setup-ingress-kind.sh" "$cluster"
printf 'Platform ready. Follow README.md to connect deploy-local and provision app credentials.\n'
