#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [cluster]\n' "${0##*/}"
  printf 'Default: cluster=deployment; requires Argo CD in namespace argocd\n'
  printf 'Requires kind port mapping 127.0.0.1:80 -> 30080; see kind/cluster.example.yaml.\n'
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if (( $# > 1 )); then
  printf 'Error: expected at most 1 argument (cluster), received %s.\n' "$#" >&2
  usage >&2
  exit 1
fi

cluster="${1:-deployment}"
context="kind-${cluster}"
kubeconfig="${INFRA_LOCAL_KUBECONFIG:-$HOME/.kube/config}"
kube=(kubectl "--kubeconfig=$kubeconfig" "--context=$context")
nodes="$(kind get nodes --name "$cluster")"

if [[ -z "$nodes" ]]; then
  printf 'Error: kind cluster %s has no nodes.\n' "$cluster" >&2
  exit 1
fi

direct_mapping=false
while IFS= read -r candidate_node; do
  port_bindings="$(docker inspect --format '{{range (index .HostConfig.PortBindings "30080/tcp")}}{{.HostIp}}:{{.HostPort}}{{"\n"}}{{end}}' "$candidate_node")"
  if [[ "$port_bindings" == "127.0.0.1:80" ]]; then
    direct_mapping=true
    break
  fi
done <<< "$nodes"

if [[ "$direct_mapping" != true ]]; then
  printf 'Error: kind cluster %s must map 127.0.0.1:80 to node port 30080.\n' "$cluster" >&2
  printf 'Follow docs/rebuild.md for a planned rebuild using kind/cluster.example.yaml.\n' >&2
  exit 1
fi

printf 'Waiting for the shared Traefik Helm controller\n'
"${kube[@]}" --namespace=traefik wait \
  --for=jsonpath='{.spec.type}'=NodePort service/traefik --timeout=180s
"${kube[@]}" --namespace=traefik \
  rollout status deployment/traefik --timeout=180s

node_port="$("${kube[@]}" -n traefik get service traefik -o jsonpath='{.spec.ports[?(@.name=="web")].nodePort}')"
if [[ "$node_port" != 30080 ]]; then
  printf 'Error: expected Traefik HTTP NodePort 30080.\n' >&2
  exit 1
fi
repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
python3 "$repository/scripts/check-gateway.py" --environment local --context "$context" --kubeconfig "$kubeconfig"
printf 'Shared Gateway is ready. Apps supply hostname-specific HTTPRoutes through Git.\n'
