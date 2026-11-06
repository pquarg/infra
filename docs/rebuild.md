# Rebuild the local platform

Creating or preparing these repositories does not alter a running cluster.
Only follow this procedure when deliberately replacing the local platform.
For an existing installation adopting the repository split, use
[the ownership transfer](../../deploy-local/docs/repository-upgrade.md#split-local-infrastructure-from-app-deployment)
instead; it needs no cluster rebuild or database move.
For adoption of the Helm installation on an existing cluster, follow
[the adoption procedure](terraform.md#adopt-an-existing-installation) instead.
An existing manifest installation must be migrated before reusing its old
Terraform state with this bootstrap.

## Preserve the deployment first

1. Pause root/app auto-sync in Argo CD and let active syncs and migration Jobs
   finish. Stop app traffic and every external database writer.
2. Follow each app's backup procedure in `deploy-local`, and verify an actual
   restore into a disposable database. Record the original database credentials,
   PostgreSQL major version and absolute host data directory outside Git.
3. Stop each database cleanly after backup and wait for its Pod to disappear.
   For `myfastapiapp`, use its [recovery and backup runbook](../../deploy-local/apps/myfastapiapp/README.md).
4. Preserve the laptop storage parent and its contents. If migrating from an
   older app-specific mount, copy the complete stopped data directory, including
   `pgdata` and its permissions, into `<app>-postgres/` under the shared parent.
   Keep the original copy. Never run two PostgreSQL servers against the same files.

PV retention does not protect against deleting the host directory or disk
failure. Cluster deletion removes namespaces, Secrets and PV/PVC objects.

## Recreate and recover

Review `kind/cluster.local.yaml` before removal. It must use the preserved
absolute storage parent, `/mnt`, the storage node label and
`127.0.0.1:80` → `30080` port mapping from `kind/cluster.example.yaml`.
Free port 80, including any obsolete ingress gateway container.
Privately back up both `terraform/argocd/terraform.tfstate` and
`terraform/platform/terraform.tfstate` and their backups if present. These files
may contain Argo CD credentials. Do not run `terraform destroy` for a rebuild;
the configuration prevents routine platform destruction and does not own the
kind cluster or laptop database files.

When the backup, clean shutdown and retained files are confirmed:

```sh
kind delete cluster --name argocd-postgres
./scripts/create-cluster.sh
./scripts/bootstrap-kind.sh
```

After completing any legacy state transfer, keep the unified Helm-based local Terraform state for this same named cluster.
The first apply refreshes against the replacement cluster and proposes
recreating both missing Helm releases in `terraform/platform`. Review the one
combined plan; there is no automatic approval.
If instead the cluster survived but the Terraform state was lost, import it
rather than treating it as a new installation.

Reconnect the deployment Git credential in Argo CD. Before registering
`deploy-local`, provision app Secrets using the original database passwords,
make its selected images available and confirm each retained directory is
mounted at the expected path. Keep the PostgreSQL major version compatible
with those data files. Register app discovery from `deploy-local` and verify
existing data and authentication before allowing traffic again.

Changing `POSTGRES_PASSWORD` in a Secret does not change an existing database
role's password. See each app's recovery runbook for deliberate rotation or
recovery of a retained PV stuck in `Released` after PVC deletion.
