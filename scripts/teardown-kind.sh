#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: teardown-kind.sh [cluster] [--dry-run] [--delete-database]
Default cluster: argocd-postgres. Targets only that local kind cluster.

Preview with --dry-run. Otherwise type "delete <cluster>" when prompted.
Before confirming, stop external writers, back up each database and verify
recovery as described in docs/rebuild.md. This script does not back up databases.

Saves cluster Secrets and local Terraform state privately, pauses Argo CD,
checks for active syncs/Jobs, drains all nodes, then deletes the cluster.
Keeps laptop database files, Terraform state, local configuration and backups.
With --delete-database, also removes legacy Kubernetes myfastapiapp pgdata AFTER
cluster deletion. This discards its old accounts, sessions and records.
The separate Compose deployment database and other apps' data are kept.
Requires Python 3 and the sibling gitops checkout for this optional operation.
Uses INFRA_LOCAL_KUBECONFIG or $HOME/.kube/config, with an explicit kind context.
USAGE
}

cluster=argocd-postgres
cluster_selected=false
dry_run=false
delete_database=false
while (( $# )); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --dry-run) dry_run=true ;;
    --delete-database) delete_database=true ;;
    -*) usage >&2; exit 1 ;;
    *)
      if [[ "$cluster_selected" == true ]]; then usage >&2; exit 1; fi
      cluster="$1"
      cluster_selected=true ;;
  esac
  shift
done
if [[ ! "$cluster" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  printf 'Error: invalid kind cluster name.\n' >&2
  exit 1
fi

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_root="$(cd -- "$project_root/.." && pwd -P)"
kubeconfig="${INFRA_LOCAL_KUBECONFIG:-$HOME/.kube/config}"
if [[ "$kubeconfig" != /* || "$kubeconfig" == *:* ]]; then
  printf 'Error: select one absolute kubeconfig file with INFRA_LOCAL_KUBECONFIG.\n' >&2
  exit 1
fi
kube=(kubectl "--kubeconfig=$kubeconfig" "--context=kind-$cluster" --request-timeout=30s)
for required_command in docker kind kubectl; do
  if ! command -v "$required_command" > /dev/null; then
    printf 'Error: %s is required.\n' "$required_command" >&2
    exit 1
  fi
done
docker info > /dev/null
clusters="$(kind get clusters)"
if ! grep -Fxq -- "$cluster" <<< "$clusters"; then
  if [[ "$delete_database" == true ]]; then
    printf 'Error: cluster %s is absent; cannot discover its actual database mount.\n' "$cluster" >&2
    printf 'Use the app-owned deletion helper with the previously recorded path; see docs/rebuild.md.\n' >&2
    exit 1
  fi
  printf 'Cluster %s is already absent. Database files and state were left untouched.\n' "$cluster"
  exit 0
fi

# A context name alone is not proof that it points to the selected kind cluster.
nodes="$(kind get nodes --name "$cluster" | sort)"
context_nodes="$("${kube[@]}" get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort)"
if [[ -z "$nodes" || "$nodes" != "$context_nodes" ]]; then
  printf 'Error: kubeconfig nodes do not match kind cluster %s; no changes made.\n' "$cluster" >&2
  exit 1
fi
database_helper="$workspace_root/gitops/apps/myfastapiapp/overlays/local/postgres/delete-data.py"
database_directory=''
if [[ "$delete_database" == true ]]; then
  if ! command -v python3 > /dev/null || [[ ! -f "$database_helper" ]]; then
    printf 'Error: --delete-database requires Python 3 and the sibling gitops database deletion helper.\n' >&2
    exit 1
  fi
  # Read live Docker mounts, never a possibly stale kind configuration file.
  database_mounts=''
  while IFS= read -r node; do
    mounts="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/mnt"}}{{.Source}}/myfastapiapp-postgres/pgdata{{"\n"}}{{else if eq .Destination "/mnt/myfastapiapp-postgres"}}{{.Source}}/pgdata{{"\n"}}{{end}}{{end}}' "$node")"
    if [[ -n "$mounts" ]]; then database_mounts+="$mounts"$'\n'; fi
  done <<< "$nodes"
  database_directory="$(printf '%s' "$database_mounts" | sed '/^$/d' | sort -u)"
  if [[ -z "$database_directory" || "$database_directory" == *$'\n'* ]]; then
    printf 'Error: expected one unambiguous database path from the live /mnt mount; no changes made.\n' >&2
    exit 1
  fi
  if [[ "$(uname -s)" == Darwin && "$database_directory" == /host_mnt/* ]]; then
    database_directory="${database_directory#/host_mnt}"
  fi
  python3 "$database_helper" --cluster "$cluster" --data-directory "$database_directory" --dry-run
fi
printf 'Remove kind cluster: %s (context kind-%s)\n' "$cluster" "$cluster"
printf 'Nodes:\n%s\n' "$nodes"
printf 'Removes ALL cluster objects, Secrets, controllers, apps and node-local images.\n'
if [[ "$delete_database" == true ]]; then
  printf 'Also ERASES myfastapiapp database accounts, sessions and records from: %s\n' "$database_directory"
  printf 'Keeps other apps\047 data, local configuration, Terraform state and backups.\n'
else
  printf 'Keeps laptop database files, local configuration, Terraform state and backups.\n'
fi
printf 'Secrets/state copies will be saved under ~/.local/share/infra-local/backups/.\n'
if [[ "$dry_run" == true ]]; then
  printf 'Preview only; no changes made.\n'
  exit 0
fi

printf '\nBefore continuing: stop external writers, back up databases and verify recovery.\n'
printf 'See docs/rebuild.md. Secrets/state copies are NOT database backups.\n'
expected_confirmation="delete $cluster"
if [[ "$delete_database" == true ]]; then expected_confirmation+=' and database'; fi
printf 'Type "%s" to confirm preparation and the deletions listed above: ' "$expected_confirmation"
confirmation=''
if ! IFS= read -r confirmation || [[ "$confirmation" != "$expected_confirmation" ]]; then
  printf '\nCancelled; no changes made.\n'
  exit 1
fi

umask 077
backup_parent="$HOME/.local/share/infra-local/backups"
mkdir -p -- "$backup_parent"
backup_parent="$(cd -- "$backup_parent" && pwd -P)"
case "$backup_parent/" in
  "$workspace_root/"*) printf 'Error: backup directory must be outside the workspace.\n' >&2; exit 1 ;;
esac
backup_dir="$(mktemp -d "$backup_parent/$cluster-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
printf 'Private Secrets/state backup: %s\n' "$backup_dir"
if [[ "$delete_database" == true ]]; then
  printf '%s\n' "$database_directory" > "$backup_dir/database-directory.txt"
fi
"${kube[@]}" get secrets --all-namespaces -o yaml > "$backup_dir/secrets.yaml"
for stage in argocd platform; do
  for state_file in "$project_root/terraform/$stage/"*.tfstate*; do
    [[ -f "$state_file" ]] || continue
    mkdir -p -- "$backup_dir/terraform/$stage"
    cp -p -- "$state_file" "$backup_dir/terraform/$stage/"
    chmod 600 "$backup_dir/terraform/$stage/"*
  done
done

maintenance_started=false
cluster_removed=false
complete=false
cleanup() {
  if [[ "$maintenance_started" == true && "$complete" != true ]]; then
    if [[ "$cluster_removed" == true ]]; then
      printf '\nCluster removed, but database cleanup failed. Recorded path: %s/database-directory.txt\n' "$backup_dir" >&2
      printf 'Inspect the error and use the app-owned helper to retry; see docs/rebuild.md.\n' >&2
      return
    fi
    printf '\nTeardown stopped. Argo CD may be paused and nodes may be cordoned.\n' >&2
    printf 'Keep writers stopped; see docs/rebuild.md#interrupted-teardown before resuming.\n' >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

application_crd="$("${kube[@]}" get crd applications.argoproj.io --ignore-not-found -o name)"
if [[ -n "$application_crd" ]]; then
  # Preserve registrations/policies privately before pausing them.
  "${kube[@]}" -n argocd get applications -o yaml > "$backup_dir/applications.yaml"
  maintenance_started=true
  applicationset_controller="$("${kube[@]}" -n argocd get deployment argocd-applicationset-controller --ignore-not-found -o name)"
  if [[ -n "$applicationset_controller" ]]; then
    "${kube[@]}" -n argocd scale "$applicationset_controller" --replicas=0
    "${kube[@]}" -n argocd wait --for=delete pod \
      -l app.kubernetes.io/name=argocd-applicationset-controller --timeout=120s
  fi
  applications="$("${kube[@]}" -n argocd get applications -o name)"
  while IFS= read -r application; do
    [[ -n "$application" ]] || continue
    "${kube[@]}" -n argocd patch "$application" --type=merge \
      -p '{"spec":{"syncPolicy":{"automated":{"enabled":false}}}}'
  done <<< "$applications"
  # Do not terminate a sync or migration. The caller can wait and rerun.
  busy_apps="$("${kube[@]}" -n argocd get applications -o go-template='{{range .items}}{{if or .operation (eq .status.operationState.phase "Running") (eq .status.operationState.phase "Terminating")}}{{.metadata.name}}{{"\n"}}{{end}}{{end}}')"
  if [[ -n "$busy_apps" ]]; then
    printf 'Error: wait for these Argo CD operations to finish, then retry:\n%s\n' "$busy_apps" >&2
    exit 1
  fi
fi
active_jobs="$("${kube[@]}" get jobs --all-namespaces -o go-template='{{range .items}}{{if .status.active}}{{.metadata.namespace}}/{{.metadata.name}}{{"\n"}}{{end}}{{end}}')"
if [[ -n "$active_jobs" ]]; then
  printf 'Error: wait for these Jobs (including migrations) to finish, then retry:\n%s\n' "$active_jobs" >&2
  exit 1
fi

maintenance_started=true
# Drain all nodes in one invocation: all are cordoned before Pods are evicted,
# so controllers cannot restart PostgreSQL on another node during shutdown.
# Respect PodDisruptionBudgets; never force deletion past a failed shutdown.
node_args=()
while IFS= read -r node; do node_args+=("$node"); done <<< "$nodes"
"${kube[@]}" drain "${node_args[@]}" --ignore-daemonsets --delete-emptydir-data --timeout=300s
kind delete cluster --name "$cluster"
remaining_clusters="$(kind get clusters)"
if grep -Fxq -- "$cluster" <<< "$remaining_clusters"; then
  printf 'Error: kind still reports cluster %s.\n' "$cluster" >&2
  exit 1
fi
cluster_removed=true
if [[ "$delete_database" == true ]]; then
  python3 "$database_helper" --cluster "$cluster" --data-directory "$database_directory" --delete
fi
complete=true
if [[ "$delete_database" == true ]]; then
  printf 'Cluster %s and myfastapiapp database files removed. Original Terraform state is retained.\n' "$cluster"
else
  printf 'Cluster %s removed. Laptop database files and original Terraform state are retained.\n' "$cluster"
fi
printf 'Private Secrets/state backup: %s\n' "$backup_dir"
printf 'To rebuild, run scripts/create-cluster.sh then scripts/bootstrap-kind.sh; see docs/rebuild.md.\n'
