# Tear down and rebuild the local platform

## Rebuild with the external Compose database

The current deployment database runs outside kind. It stays running, and its
external Docker volume survives cluster deletion. No database stop, data move or
reset is needed just to replace Kubernetes. Take and verify a
[Compose database backup](database.md#backup-and-restore) before planned disruption.
Pause app auto-sync, wait for active migrations and stop traffic/writers when
needed to preserve the recovery point. Preserve app/Git/registry credentials and
Terraform state privately: the cluster's Secrets disappear during deletion.

Preview with `./scripts/teardown-kind.sh --dry-run`. Only when cluster deletion
is deliberately authorized, run `./scripts/teardown-kind.sh` and type its selected
cluster confirmation. This drains and deletes only kind and backs up its Secrets
and local state; it does not touch the Compose database or external volume.
Keep the existing ignored kind config and legacy mounts/data for any unmigrated
apps. There is no need to replace them with the fresh-cluster example.

Recreate the cluster with `./scripts/create-cluster.sh`, then install controllers
with `./scripts/bootstrap-kind.sh` and review its Terraform apply. Confirm the
separate database is healthy with `./scripts/local-database.sh status` (use `up`
if stopped). Follow [local app setup](../../gitops/docs/local/setup.md) to recreate
namespace/connection and registry Secrets, selected images and Git access. Run
the Pod connectivity probe before registering apps. Verify migrations, readiness
and existing login/records before traffic. Database recovery/reset follows
[database.md](database.md), independently of the cluster.

## Legacy Kubernetes database rebuild

The remaining methods apply only to installations still using the retained
PostgreSQL StatefulSet and laptop storage. The `--delete-database` flag removes
only that legacy laptop data; it does not delete the external Compose volume.
For migration without a cluster rebuild, use the
[database cutover](../../gitops/docs/local/database-migration.md).

Choose [method 1 to keep the database](#method-1-keep-the-database) or
[method 2 to destroy the database](#method-2-destroy-the-database). Both delete
the entire kind cluster and all Kubernetes objects and Secrets. Only method 2
also removes the selected app's laptop database files. Run commands from
`infra-local` unless a step explicitly changes directory. These examples use
cluster `argocd-postgres` and context `kind-argocd-postgres`.

Creating or preparing these repositories does not alter a running cluster.
Only follow this procedure when deliberately replacing the local platform.
For an existing installation adopting the repository split, use
[the ownership transfer](../../gitops/docs/local/repository-upgrade.md#split-local-infrastructure-from-app-deployment)
instead; it needs no cluster rebuild or database move.
For adoption of the Helm installation on an existing cluster, follow
[the adoption procedure](terraform.md#adopt-an-existing-installation) instead.
An existing manifest installation must be migrated before reusing its old
Terraform state with this bootstrap.

## Prepare for either method

1. Pause root/app auto-sync in Argo CD and let active syncs and migration Jobs
   finish. Stop app traffic and every external database writer.
2. Stop each app's database writers. For `myfastapiapp`:

   ```sh
   kubectl --context kind-argocd-postgres -n myfastapiapp scale \
     deployment/myfastapiapp-deployment --replicas=0
   kubectl --context kind-argocd-postgres -n myfastapiapp wait \
     --for=delete pod -l app=myfastapiapp-pod --timeout=120s
   ```

3. For method 1, follow each app's backup procedure and verify an actual restore
   into a disposable database. For method 2, do this if you may need the discarded
   data later. For `myfastapiapp`, run from `infra-local`:

   ```sh
   ../gitops/apps/myfastapiapp/overlays/local/postgres/backup.sh
   ```

   This saves a private archive outside Git and checks archive readability; it
   does not test a restore. See the [app backup runbook](../../gitops/apps/myfastapiapp/overlays/local/README.md#back-up-and-release-a-database-change).
   Record the original database credentials, PostgreSQL major version and
   absolute host data directory outside Git. Retained data needs the original
   credentials; Kubernetes Secrets disappear with the cluster. Preserve filled
   app Secret files and access to Git/GHCR credentials privately.
4. Stop each database cleanly after backup and wait for its Pod to disappear.
   For `myfastapiapp`:

   ```sh
   kubectl --context kind-argocd-postgres -n myfastapiapp scale \
     statefulset/myfastapiapp-postgres-statefulset --replicas=0
   kubectl --context kind-argocd-postgres -n myfastapiapp wait \
     --for=delete pod -l app=myfastapiapp-postgres-pod --timeout=180s
   ```

   Continue only after shutdown succeeds. Never run two PostgreSQL servers
   against the same files.

PV retention does not protect against deleting the host directory or disk
failure. Cluster deletion removes namespaces, Secrets and PV/PVC objects.

Review `kind/cluster.local.yaml` before removal. It must use the preserved
absolute storage parent, `/mnt`, the storage node label and
`127.0.0.1:80` → `30080` port mapping from `kind/cluster.example.yaml`.
Free port 80, including any obsolete ingress gateway container.
Privately back up both `terraform/argocd/terraform.tfstate` and
`terraform/platform/terraform.tfstate` and their backups if present. These files
may contain Argo CD credentials. Do not run `terraform destroy` for a rebuild;
the configuration prevents routine platform destruction and does not own the
kind cluster or laptop database files.

## Method 1: keep the database

After completing preparation, use the helper to preview and perform teardown:

```sh
./scripts/teardown-kind.sh --dry-run
./scripts/teardown-kind.sh
```

Type `delete argocd-postgres` at the prompt. The script saves cluster Secrets,
Argo CD Application definitions/policies (when installed), and both local
Terraform states privately under `$HOME/.local/share/infra-local/backups/` before
changing cluster resources. It stops the ApplicationSet controller, disables
Application auto-sync and checks for pending/running syncs and active Jobs.
If any remain, wait for completion and rerun. It drains all nodes together,
respecting PodDisruptionBudgets and Pod shutdown grace periods, then deletes
the named cluster. It preserves all laptop database files, original Terraform
state, configuration and existing backups. No `terraform destroy` is run.

The script's Secrets/state copies do not back up PostgreSQL or prove that a
database backup can be restored. Complete the preparation above first.
For other clusters, pass the cluster name; every kubectl call selects
`kind-<cluster>` explicitly. Set `INFRA_LOCAL_KUBECONFIG` for another absolute
kubeconfig file. A mismatched node inventory stops teardown before changes.

Alternatively, after the same preparation, manually delete the cluster:

```sh
kind delete cluster --name argocd-postgres
```

Preserve the laptop storage parent and all app data, including `pgdata` and its
permissions. If migrating from an older app-specific mount, follow the
[storage upgrade](../../gitops/docs/local/repository-upgrade.md#shared-storage-at-the-next-rebuild)
to copy the complete stopped directory into `<app>-postgres/` under the shared
parent. Keep the original copy. Continue with [startup](#spin-up-after-either-method)
using the original passwords and a compatible PostgreSQL major version.

## Method 2: destroy the database

After completing preparation and deciding to discard myfastapiapp's data,
preview and run the combined teardown:

```sh
./scripts/teardown-kind.sh --delete-database --dry-run
./scripts/teardown-kind.sh --delete-database
```

This optional argument requires Python 3 and the sibling `gitops` checkout.
It discovers one unambiguous host data path from the live kind nodes' `/mnt`
mount (or `/mnt/myfastapiapp-postgres` app mount), validates it with the
[app-owned deletion helper](../../gitops/apps/myfastapiapp/overlays/local/postgres/delete-data.py),
and shows it before requiring `delete argocd-postgres and database`. It does
not use the potentially edited kind YAML to select a deletion path. App/data
directories must not be symlinks; nonempty data must contain a valid
`PG_VERSION`. Other running containers must not mount this storage.

Only after a successful drain and verified cluster deletion does it remove
myfastapiapp's `pgdata`. It preserves the app directory, shared storage parent,
other apps' data, original state, filled Secret files and backups. The data
path is recorded privately as `database-directory.txt` with the Secrets/state
backup. This argument does not discard additional apps' databases.

Alternatively, delete just the cluster with `./scripts/teardown-kind.sh`, or manually:

```sh
kind delete cluster --name argocd-postgres
```

Cluster deletion alone leaves all laptop database files intact. If you did
not use `--delete-database`, follow each selected app's physical removal procedure, starting with
[myfastapiapp](../../gitops/apps/myfastapiapp/overlays/local/postgres/reset.md#discard-the-database-after-cluster-teardown).
Do this before recreating the cluster; delete only that app's data and keep
the shared storage parent, other apps' directories and Terraform state.

Continue with [startup](#spin-up-after-either-method). Empty storage causes
PostgreSQL to initialize a fresh database; migrations recreate the schema.
Old accounts, sessions and records will be gone. Backups remain outside the
data directory and are not removed by this procedure.

## Interrupted teardown

If the helper fails, do not assume the cluster was removed. It never forces a
failed drain or bypasses a PodDisruptionBudget. Inspect its error, let active
syncs/migrations finish or resolve the shutdown problem, then rerun. A drain
failure can leave nodes cordoned, some workloads stopped, Application auto-sync
disabled and the ApplicationSet controller scaled to zero. Keep external
writers stopped until you either finish teardown or deliberately resume service.

To abandon teardown, inspect nodes and Pods using context
`kind-argocd-postgres`. Uncordon each affected node explicitly with
`kubectl --context kind-argocd-postgres uncordon <node>`, restore the ApplicationSet
controller's prior replica count, and restore Application auto-sync policies
from the private `applications.yaml` snapshot. Repeated attempts take new
snapshots; use the one from before the first pause. Restore only the reviewed
policies rather than blindly applying saved transient Application status.
Verify database/app health and existing login before resuming traffic.

On successful deletion, use startup below instead. Saved Secrets include
database, registry and Git credentials; use them privately to recreate the
required Secrets before registering apps. Do not commit these snapshots or
indiscriminately apply cluster-generated Secrets to the replacement cluster.

If `--delete-database` removed the cluster but file removal failed (for example,
because of directory permissions), it reports failure and keeps the recorded
path with the private backup. Do not recreate the cluster until cleanup is
resolved. The wrapper cannot rediscover mounts after cluster removal. Inspect
`database-directory.txt`, then retry the app helper using that exact path:

```sh
python3 ../gitops/apps/myfastapiapp/overlays/local/postgres/delete-data.py \
  --cluster argocd-postgres \
  --data-directory '/absolute/storage/parent/myfastapiapp-postgres/pgdata' --dry-run
```

Replace the placeholder with the recorded path. After review, replace
`--dry-run` with `--delete` to finish removing the data. The helper checks again
that the cluster is absent and no running Docker container mounts the storage.
Use an account permitted to remove this exact directory; do not broaden the
deletion or remove shared storage. An already absent `pgdata` is a successful
no-op. Preserve the app directory for the next PV.

## Spin up after either method

Start Docker Desktop, ensure port 80 is free, and confirm the configured host
storage parent and each app subdirectory exist. From `infra-local`:

```sh
./scripts/create-cluster.sh
./scripts/bootstrap-kind.sh
```

After completing any legacy state transfer, keep the unified Helm-based local Terraform state for this same named cluster.
The first apply refreshes against the replacement cluster and proposes
recreating both missing Helm releases in `terraform/platform`. Review the one
combined plan; there is no automatic approval.
If instead the cluster survived but the Terraform state was lost, import it
rather than treating it as a new installation.

Reconnect the deployment Git credential in Argo CD using the
[UI/sign-in instructions](../README.md#2-install-argo-cd-and-shared-ingress).
Before registering `gitops`:

1. [Recreate the app namespace and database Secrets](../../gitops/apps/myfastapiapp/overlays/local/README.md#provision-credentials).
   For method 1, reuse the original passwords and keep the PostgreSQL major
   version compatible with the retained files. For method 2, you may use new
   matching credentials in both app/database Secrets.
2. Recreate [registry credentials](../../gitops/docs/local/credentials.md#registry-authentication)
   and [make the selected images available](../../gitops/apps/myfastapiapp/overlays/local/README.md#make-the-app-image-available).
   Images loaded into the deleted kind cluster must be loaded again.
3. Confirm retained data is mounted at `/mnt/<app>-postgres`, or that the
   selected app directory is empty for method 2. Any changed app manifests must
   be validated and published: Argo CD reads GitHub `main`, not local files.

Register app discovery:

```sh
cd ../gitops
./scripts/bootstrap.sh --environment local --context kind-argocd-postgres
```

This starts automatic sync and migrations. Root **Synced** only confirms
registration. Wait for each app's **Synced / Healthy** status and follow its
[verification procedure](../../gitops/apps/myfastapiapp/overlays/local/README.md#verify-the-deployment).
For method 1, verify existing data and login; for method 2, register a new account
and verify login. Resume traffic only after these checks pass.

Changing `POSTGRES_PASSWORD` in a Secret does not change an existing database
role's password. See each app's recovery runbook for deliberate rotation or
recovery of a retained PV stuck in `Released` after PVC deletion.
