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
3. Preview teardown from `infra/environments/local`:

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
6. Follow [local app setup](https://github.com/pquarg/gitops/blob/main/docs/local/setup.md) to recreate the
   namespace, connection and registry Secrets, selected images and Git access.
   Images loaded into the deleted cluster must be loaded again. Run the Pod
   connectivity probe before registering apps, then check migrations, readiness,
   existing login and records before traffic.

No database stop or data move is needed just to replace kind. Database
[recovery or deliberate reset](database.md) is independent of cluster replacement.
Do not use `terraform destroy`: kind owns the cluster, and all three platform
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

Gateway API recovery also requires the Terraform-owned Standard CRD release,
GatewayClass and shared local Gateway. Recover its Helm binding before applying
if the cluster survives; after an authorized rebuild the full platform apply
installs CRDs before Traefik. Restore app HTTPRoutes through GitOps after Secrets
and database probes are ready. Never delete CRDs as a routing rollback.


## Preserve credentials and maintenance policies during recovery

Save the original Application policies and workload replica settings privately
**before** pausing writers or reconcilers for database backup. A later teardown
snapshot may already contain the paused settings. Verify backups of both app
databases; tracker backup/restore is separate from demo-api's helper. PostgreSQL
and its external volume stay running throughout a kind-only rebuild.

Before deleting kind, preserve the selected locally loaded image releases in a
private archive. Prefer the exact running content/digests; rebuilding the same
mutable tag is not evidence of image equivalence. Reload the archived images
into the replacement cluster before the Pod database probes and app registration.

After the platform apply, restore the app connection/registry Secrets and Argo
repository, administrator and UI TLS Secrets using sanitized metadata. Recreate
app namespaces first. Restore neither old Helm release records nor old
ServiceAccount tokens. Leave fresh generated internal credentials such as Redis
consistent with the new controllers. Never apply an entire Secrets snapshot
blindly; it is private recovery material, not a desired-state manifest.

To preserve an app that was already paused, temporarily scale the new
ApplicationSet controller to zero before bootstrap. Bootstrap can still install
its ApplicationSet from Git. Recreate the saved app specifications with their
original sync policies, using that **new** ApplicationSet UID for owner references,
then resume the ApplicationSet controller's original replica count. Omit saved
Application status, operations, resourceVersion and old owner UIDs. The source
repository/path/revision and destination must match the current local contract.
Run the paused app's one-time full hook-enabled sync deliberately; do not silently
re-enable its auto-sync. Recheck repository access, controller readiness and all
Application policies after recovery.

## Verified Gateway rebuild

On 16 November 2026, kind `deployment` was rebuilt with pinned Kubernetes
`v1.36.4`. A bare cluster with no CRDs/releases accepted one full platform plan
creating the three Helm releases. Standard CRDs completed before Traefik started;
no targeted apply or Terraform state removal was needed. The controller/chart
pins, localhost mapping, legacy mounts and database volume were preserved.

Both apps passed their Pod database probes and full migration hooks after image
and credential restoration. Gateway route status, health/readiness, existing
synthetic logins/sessions, tracker uploads/downloads/parsing/search profiles,
account isolation and API/frontend path behavior passed. Admin/TLS Secret data
and original sync policies were preserved. This is local runtime evidence; no AWS
deployment or paid discovery was performed.
