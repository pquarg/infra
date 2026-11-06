# Tear down and rebuild the local platform

Choose [method 1 to keep the database](#method-1-keep-the-database) or
[method 2 to destroy the database](#method-2-destroy-the-database). Both delete
the entire kind cluster and all Kubernetes objects and Secrets. Only method 2
also removes the selected app's laptop database files. Run commands from
`infra-local` unless a step explicitly changes directory. These examples use
cluster `argocd-postgres` and context `kind-argocd-postgres`.

Creating or preparing these repositories does not alter a running cluster.
Only follow this procedure when deliberately replacing the local platform.
For an existing installation adopting the repository split, use
[the ownership transfer](../../deploy-local/docs/repository-upgrade.md#split-local-infrastructure-from-app-deployment)
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
   ../deploy-local/apps/myfastapiapp/postgres/backup.sh
   ```

   This saves a private archive outside Git and checks archive readability; it
   does not test a restore. See the [app backup runbook](../../deploy-local/apps/myfastapiapp/README.md#back-up-and-release-a-database-change).
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

After completing preparation, delete the cluster:

```sh
kind delete cluster --name argocd-postgres
```

Preserve the laptop storage parent and all app data, including `pgdata` and its
permissions. If migrating from an older app-specific mount, follow the
[storage upgrade](../../deploy-local/docs/repository-upgrade.md#shared-storage-at-the-next-rebuild)
to copy the complete stopped directory into `<app>-postgres/` under the shared
parent. Keep the original copy. Continue with [startup](#spin-up-after-either-method)
using the original passwords and a compatible PostgreSQL major version.

## Method 2: destroy the database

After completing preparation and deciding which app data to discard, delete
the cluster:

```sh
kind delete cluster --name argocd-postgres
```

Cluster deletion alone leaves all laptop database files intact. Next, follow
each selected app's physical removal procedure, starting with
[myfastapiapp](../../deploy-local/apps/myfastapiapp/postgres/reset.md#discard-the-database-after-cluster-teardown).
Do this before recreating the cluster; delete only that app's data and keep
the shared storage parent, other apps' directories and Terraform state.

Continue with [startup](#spin-up-after-either-method). Empty storage causes
PostgreSQL to initialize a fresh database; migrations recreate the schema.
Old accounts, sessions and records will be gone. Backups remain outside the
data directory and are not removed by this procedure.

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
Before registering `deploy-local`:

1. [Recreate the app namespace and database Secrets](../../deploy-local/apps/myfastapiapp/README.md#provision-credentials).
   For method 1, reuse the original passwords and keep the PostgreSQL major
   version compatible with the retained files. For method 2, you may use new
   matching credentials in both app/database Secrets.
2. Recreate [registry credentials](../../deploy-local/docs/credentials.md#registry-authentication)
   and [make the selected images available](../../deploy-local/apps/myfastapiapp/README.md#make-the-app-image-available).
   Images loaded into the deleted kind cluster must be loaded again.
3. Confirm retained data is mounted at `/mnt/<app>-postgres`, or that the
   selected app directory is empty for method 2. Any changed app manifests must
   be validated and published: Argo CD reads GitHub `main`, not local files.

Register app discovery:

```sh
cd ../deploy-local
./scripts/bootstrap.sh --context kind-argocd-postgres
```

This starts automatic sync and migrations. Root **Synced** only confirms
registration. Wait for each app's **Synced / Healthy** status and follow its
[verification procedure](../../deploy-local/apps/myfastapiapp/README.md#verify-the-deployment).
For method 1, verify existing data and login; for method 2, register a new account
and verify login. Resume traffic only after these checks pass.

Changing `POSTGRES_PASSWORD` in a Secret does not change an existing database
role's password. See each app's recovery runbook for deliberate rotation or
recovery of a retained PV stuck in `Released` after PVC deletion.
