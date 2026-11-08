#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [cluster] [config]\n' "${0##*/}"
  printf 'Defaults: cluster=deployment; config=kind/cluster.local.yaml in this repository.\n'
  printf 'Creates a new cluster only; existing clusters are never replaced.\n'
}
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# > 2 )); then
  usage >&2
  exit 1
fi
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cluster="${1:-deployment}"
config="${2:-$project_root/kind/cluster.local.yaml}"
for required_command in docker kind kubectl; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required; see README.md.\n' "$required_command" >&2
    exit 1
  fi
done
if [[ ! -f "$config" ]]; then
  printf 'Error: missing config %s. Copy kind/cluster.example.yaml first.\n' "$config" >&2
  exit 1
fi
docker info > /dev/null
existing_clusters="$(kind get clusters)"
while IFS= read -r existing_cluster; do
  if [[ "$existing_cluster" == "$cluster" ]]; then
    printf 'Error: cluster %s already exists. See docs/rebuild.md; no changes made.\n' "$cluster" >&2
    exit 1
  fi
done <<< "$existing_clusters"
kind create cluster --name "$cluster" --config "$config" --wait 180s
kubectl --context="kind-${cluster}" get nodes --show-labels
printf 'Cluster created. Next run %s/scripts/bootstrap-kind.sh %s\n' "$project_root" "$cluster"
