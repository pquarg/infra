#!/usr/bin/env bash
# Read-only checks shared by local planning and interactive bootstrap.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cluster="${1:?select cluster}"
kubeconfig="${2:?select one kubeconfig file}"
[[ "$cluster" =~ ^[a-z0-9][a-z0-9-]*$ && "$kubeconfig" == /* && "$kubeconfig" != *:* ]] || { printf 'Error: select a valid cluster and one absolute kubeconfig file.\n' >&2; exit 1; }
kube=(kubectl "--kubeconfig=$kubeconfig" "--context=kind-$cluster")
umask 077
# Check the named cluster exists before applying anything; never use the ambient context.
nodes="$(kind get nodes --name "$cluster" | sort)"
selected="$("${kube[@]}" get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort)"
[[ -n "$nodes" && "$nodes" == "$selected" ]] || { printf 'Error: context nodes do not match the selected kind cluster.\n' >&2; exit 1; }
# Verify the state belongs to this cluster and contains only platform releases.
tracked_resources=""
platform_state="$project_root/platform/terraform.tfstate"
if [[ -f "$platform_state" ]]; then
  tracked_context="$(terraform -chdir="$project_root" output "-state=$platform_state" -raw context 2>/dev/null || true)"
  if [[ -n "$tracked_context" && "$tracked_context" != "kind-$cluster" ]]; then
    printf 'Error: platform state belongs to %s. Use separate state/checkouts for different clusters.\n' "$tracked_context" >&2
    exit 1
  fi
  tracked_resources="$(terraform -chdir="$project_root" state list "-state=$platform_state")"
  while IFS= read -r resource; do
    case "$resource" in
      ''|helm_release.argocd|helm_release.traefik) ;;
      *) printf 'Error: unexpected resource in platform state: %s. Inspect ownership before applying.\n' "$resource" >&2; exit 1 ;;
    esac
  done <<< "$tracked_resources"
fi
# Refuse existing releases not tracked by this checkout.
for release in argocd traefik; do
  if ! grep -Fxq "helm_release.$release" <<< "$tracked_resources"; then
    deployment="$release"
    if [[ "$release" == argocd ]]; then deployment=argocd-server; fi
    existing="$("${kube[@]}" -n "$release" get deployment "$deployment" --ignore-not-found -o name)"
    # Also detect a real Helm record even if its Deployment is absent.
    records="$("${kube[@]}" -n "$release" get secrets -l "owner=helm,name=$release" -o name)"
    if [[ -n "$existing" || -n "$records" ]]; then
      printf 'Error: existing %s is untracked. Inspect state recovery in docs/terraform.md#recover-platform-state.\n' "$release" >&2
      exit 1
    fi
  fi
done
