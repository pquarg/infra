# Tear down and rebuild the local platform

Examples use cluster `deployment` and context `kind-deployment`. For another
cluster, use its actual name/context throughout and retain its own Terraform
checkout/state. Rebuilding deletes all Kubernetes objects, Secrets and
node-local images. The separate Compose PostgreSQL database and external
Docker volume remain running and unchanged.

## Rebuild with the external Compose database

1. Take and verify a [database backup](database.md#backup-and-restore) before
   planned disruption. Pause app auto-sync, wait for active migrations and stop
   traffic/writers when needed to preserve the recovery point. Preserve private
   password files, app/Git/registry credentials and platform state outside Git.
2. Review `kind/cluster.local.yaml`. Use the intended cluster name, localhost
   port mapping and pinned `nodes[].image` from `kind/cluster.example.yaml`.
   Changing this configuration does not change a running cluster. PostgreSQL
   needs no kind storage mount.
3. Preview teardown from `infra-local`:

   ```sh
   ./scripts/teardown-kind.sh --dry-run
   ```

4. Only when cluster deletion is deliberately authorized, run:

   ```sh
   ./scripts/teardown-kind.sh
   ```

   Type `delete deployment` to confirm. For another cluster pass its name.
   The script checks that `kind-<cluster>` targets the same nodes reported by
   kind. It privately saves Secrets, Application policies and platform state
   under `$HOME/.local/share/infra-local/backups/`, pauses Argo CD, checks for
   active syncs/Jobs, drains all nodes and deletes the selected cluster.
   It does not back up or reset the database. A failed shutdown stops deletion.
5. Recreate the cluster and install the controllers:

   ```sh
   ./scripts/create-cluster.sh
   ./scripts/bootstrap-kind.sh
   ./scripts/local-database.sh status
   ```

   Keep platform state for this same named cluster. Terraform refreshes it and
   proposes recreating the missing releases; review the combined apply. If the
   separate database was stopped, start it with `./scripts/local-database.sh up`.
6. Follow [local app setup](../../gitops/docs/local/setup.md) to recreate the
   namespace, connection and registry Secrets, selected images and Git access.
   Images loaded into the deleted cluster must be loaded again. Run the Pod
   connectivity probe before registering apps, then check migrations, readiness,
   existing login and records before traffic.

No database stop or data move is needed just to replace kind. Database
[recovery or deliberate reset](database.md) is independent of cluster replacement.
Do not use `terraform destroy`: kind owns the cluster, and both controller
releases have `prevent_destroy`.

## Interrupted teardown

If teardown fails, inspect its error before retrying. It never forces a failed
drain or bypasses a PodDisruptionBudget. A partial run can leave nodes cordoned,
workloads stopped, Application auto-sync disabled and the ApplicationSet
controller scaled to zero. Keep writers stopped until teardown finishes or
service is deliberately resumed.

To resume service, inspect nodes and Pods using context `kind-deployment`.
Uncordon each affected node explicitly with
`kubectl --context kind-deployment uncordon <node>`, restore the ApplicationSet
controller's prior replica count, and restore Application auto-sync policies
from the private `applications.yaml` snapshot. Repeated attempts create new
snapshots; use the one from before the first pause. Restore only reviewed
policies rather than applying a saved Application status. Verify app/database
health and existing login before traffic.

On successful deletion, follow the rebuild steps above. Saved Secrets contain
credentials; use them privately to recreate required Secrets. Keep original
password files, configuration, state and backup archives. A Git revert cannot
restore deleted Kubernetes objects or Secrets.
