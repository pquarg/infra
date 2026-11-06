# Local infrastructure

Prepare the local Kubernetes platform used by the sibling `gitops`
repository. This is an independent Git repository. It owns the kind cluster,
host port and storage mappings, pinned Argo CD installation, and shared Traefik
controller, plus the separate local deployment PostgreSQL container and its
backup/restore tooling. Application code lives in app repositories; app workloads,
connection Secrets and migration Jobs stay in `gitops`. Existing Kubernetes
database manifests remain there only for migration and legacy recovery.

```text
deployment-project/
  infra-local/                  # This repository
    kind/cluster.example.yaml    # Cluster and localhost ingress port
    database/                   # Separate deployment PostgreSQL Compose service
    argocd/chart-version.txt     # Helm chart 10.11.0 (Argo CD v3.5.5)
    argocd/values.yaml           # Argo CD settings
    traefik/                    # Chart 42.0.0 and exact shared ingress values
    terraform/platform/         # One local state: Argo CD and Traefik Helm releases
    scripts/                    # Create, bootstrap, readiness checks and validation
    docs/terraform.md            # Ownership, plans, state and existing-cluster adoption
    docs/rebuild.md              # Cluster rebuild; legacy storage recovery
  gitops/                       # Shared app bases, environment overlays and releases
  apps/myfastapiapp/             # Application code and image build
```

Platform setup reads this checkout directly. Terraform installs both shared
controllers through pinned Helm releases. There is no platform Git-watching
Application and no Argo CD Application for Traefik after migration. The separate
`gitops` root Application discovers apps from Git. Argo CD reconciles only
application deployments; a platform change takes effect after a reviewed Terraform
apply. Read [the Terraform guide](docs/terraform.md) before adopting an existing
installation. kind remains responsible for the cluster, ports and storage mounts.

## Guided local setup

With the prerequisites below installed and Docker Desktop running:

```sh
./scripts/setup-local.sh --check
./scripts/setup-local.sh
```

This repeatable entry point prepares the separate database, creates/reuses kind,
runs the reviewed Terraform bootstrap, provisions app/Git connection Secrets,
builds and loads the selected app image, checks Pod database connectivity, registers
local GitOps and verifies migrations, app sync and HTTP readiness. It asks for a
read-only GitHub deploy-key file when Argo CD has no existing Git credentials.
See [setup details and recovery](docs/setup-local.md), including one-time key setup.
It preserves existing configuration/passwords and stops at legacy migration or
credential mismatches. The manual steps below remain available.

## Prerequisites

Install Docker Desktop (and start it), kind, kubectl and Terraform
`1.17.x` (CI uses `1.17.1`). The platform root pins the Helm provider to `3.3.0` and has a committed
provider lock file. The existing deployment
validation targets Kubernetes `1.36.0`,
kubectl `v1.36.1`, kind `v0.33.0`; the cluster example uses kind's default node
image. For validation also install Helm 3 (CI uses `v3.19.0`) and kubeconform
`v0.8.0`. Helm CLI renders the chart for validation and is used for one-time
adoption; normal bootstrap uses Terraform's Helm provider without the Helm CLI.
Docker Compose is required for validation; its CLI validates the database
configuration without needing a running Docker daemon. Python **3.11 or newer**
is required for the setup entry point and its validation tests. The optional
`teardown-kind.sh --delete-database` operation needs Python 3 and the sibling
`gitops` checkout. Internet access is required to
download providers, images, pinned Helm charts and validation schemas.

Ports `127.0.0.1:80` and `127.0.0.1:5433` must be available. The deployment
database uses Docker Desktop host routing; see [database setup](docs/database.md).
For legacy mounted databases, Docker Desktop must still allow sharing the original
laptop storage directory. Existing clusters should follow
[the repository split instructions](../gitops/docs/local/repository-upgrade.md#split-local-infrastructure-from-app-deployment)
before adopting the new ownership; creating a second cluster is unnecessary.

## 1. Prepare the database and cluster

From this repository, follow [database setup](docs/database.md#first-setup) to
configure private password files, create the separate external Docker volume and
start PostgreSQL. Existing Kubernetes databases must use the
[cutover runbook](../gitops/docs/local/database-migration.md); preparing configuration
does not migrate records.

For a **new** kind cluster:

```sh
cp -n kind/cluster.example.yaml kind/cluster.local.yaml
./scripts/create-cluster.sh
```

The default cluster is `argocd-postgres`, context `kind-argocd-postgres`. The
script refuses to replace an existing cluster. The current example needs no
database storage mount. Keep existing ignored kind files, `/mnt` mounts, node
labels and laptop data unchanged for legacy recovery or other apps. Older
mount-based setups can refer to `kind/cluster-storage.example.yaml`.

To select another name/config, use `./scripts/create-cluster.sh another-cluster
/absolute/config.yaml` and pass that name to platform scripts. Each platform
Terraform state must belong to only one cluster; use separate checkouts for
other clusters. Existing clusters need no rebuild for the database cutover.

## 2. Install Argo CD and shared ingress

For an **existing Argo CD installation**, first follow
[adoption](docs/terraform.md#adopt-an-existing-installation). A manifest-based
installation needs a reviewed Helm ownership migration; importing a Helm release
alone cannot convert it. Bootstrap stops if it finds legacy manifest state or an
existing untracked controller or Helm record. Do not rebuild the cluster to switch tools.
On a fresh cluster, skip adoption.

```sh
./scripts/bootstrap-kind.sh
```

This initializes `terraform/platform`, asks you to review and approve one apply
for both Helm releases, then verifies Argo CD CRDs/controllers, Traefik, its
IngressClass/NodePort and the kind host port mapping. It registers no app.
Before init/apply it checks both legacy state locations, rejects a remaining
Traefik Application, and refuses untracked controllers or Helm release records.
An existing installation must complete the [ownership transfer](docs/terraform.md#adopt-an-existing-installation).
The providers/scripts select `kind-<cluster>` from `~/.kube/config` explicitly.
Set `INFRA_LOCAL_KUBECONFIG` to one absolute file to select another kubeconfig;
the wrapper's cluster/file arguments override ignored Terraform variables.

To investigate without applying, use the [manual init/plan commands](docs/terraform.md#inspect-and-apply-manually).

To open the Argo CD UI, leave this running in another terminal:

```sh
kubectl --context kind-argocd-postgres -n argocd \
  port-forward service/argocd-server 8080:443
```

Open https://localhost:8080 and sign in as `admin`. Retrieve the initial
password on a fresh installation:

```sh
kubectl --context kind-argocd-postgres -n argocd \
  get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
printf '\n'
```

Use the replacement password if it was already changed. Under **Settings →
Repositories**, connect `git@github.com:pquarg/gitops.git` with a read-only
Git credential usable by the `default` project. Keep the credential outside Git.
If using another remote, change all `repoURL` values in `gitops`'s
`root/staging.yaml` and `argocd/local/apps.yaml` to match. Git authentication is
separate from the app's GHCR image-pull authentication.

## 3. Hand over to gitops

Existing deployment registrations must follow [GitOps cutover](../gitops/docs/repository-migration.md)
before switching from the old deployment repositories.

The platform provides this contract:

| Setting | Value |
| --- | --- |
| Default context | `kind-argocd-postgres` |
| Argo CD namespace | `argocd`, with Application/ApplicationSet CRDs ready |
| Shared ingress | Namespace `traefik`, IngressClass `traefik` |
| HTTP mapping | `127.0.0.1:80` → node `30080` → Traefik |
| Deployment database | Separate Compose PostgreSQL, reachable from kind via `host.docker.internal:5433` |
| Database persistence | External Docker volume `deployment-project-myfastapiapp-postgres` |

From `gitops`, follow [deployment setup](../gitops/docs/local/setup.md):
provision the app namespace, connection and registry Secrets, make its selected
image available, run the Pod database connectivity probe, validate and publish
the deployment manifests, then run:

```sh
cd ../gitops
./scripts/bootstrap.sh --environment local --context kind-argocd-postgres
```

That separate bootstrap registers `staging-root`; its ApplicationSet discovers
`apps/*/overlays/local` from GitHub `main`. App sync is automatic, including schema
migrations. The separate database, connection Secret and any restored records
must be ready first. The
root no longer owns Traefik. Publishing app releases does not recreate the
cluster. Verify app readiness using its own deployment README.

## Validate and update

```sh
./scripts/validate.sh
git diff --check
```

Validation renders both pinned Helm charts, checks
Terraform formatting/configuration, Compose configuration, database helper safety
tests, Bash bootstrap safety tests, Kubernetes/Argo CD
schemas and Bash syntax, and needs no running cluster or AWS credentials.
Terraform initialization uses temporary configuration copies, `-backend=false`
and the committed provider locks; it downloads providers without reading or
changing existing state, local variables or working provider directories. The
three upstream CRD definitions are explicitly skipped because the schema
registry has no `CustomResourceDefinition` schema; the remaining installation
resources (including Helm hook Jobs) and Traefik resources are checked
strictly. Unknown platform resource kinds still fail. CI runs the same checks.
kind configuration is consumed by cluster creation; these checks do not validate kind YAML, CRD
definition semantics or runtime readiness.

With Docker Desktop running, `bash tests/database-integration.sh` separately
tests app privileges, host routing, legacy dump/restore ownership and external
volume retention using only a temporary project and volume. It cleans up those
test resources and does not start the deployment database or create a cluster.

When changing provider pins, [refresh the lock file for both macOS ARM64 and
Linux AMD64](docs/terraform.md#maintain-provider-checksums) before committing.
CI and bootstrap use the committed checksums with `-lockfile=readonly`.

After editing `argocd/values.yaml` or `traefik/values.yaml`, review the platform plan:

```sh
umask 077
terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform plan -out=platform.tfplan
terraform -chdir=terraform/platform show platform.tfplan
terraform -chdir=terraform/platform apply platform.tfplan
./scripts/setup-ingress-kind.sh
```

These manual examples use the default context. Copy
`terraform/platform/terraform.tfvars.example` to ignored `terraform.tfvars` for
another cluster/file. Settings and chart pins live in each controller's
`values.yaml` and `chart-version.txt`; Terraform and validation read them directly.
Do not also manage these controllers through Argo CD or `kubectl apply`.
Cluster port or mount changes require a [planned rebuild](docs/rebuild.md);
editing YAML cannot change an existing node's ports or mounts.

The moved `kind/postgres-config.local.yaml`, if present in this checkout, is
the previous machine-specific configuration. It remains ignored and is not
automatically selected by the scripts. Review it before reuse; do not replace
an existing cluster or relocate data merely to match the new example.

## Tear down and spin up again

For the current external database, follow [cluster rebuild](docs/rebuild.md#rebuild-with-the-external-compose-database).
Cluster teardown preserves the running Compose database and its external volume,
including when the legacy `--delete-database` option is supplied. Database
backup, restore and deliberate reset are [separate procedures](docs/database.md).

**The database-file removal methods below apply only to the legacy Kubernetes
database.** They are retained for existing installations and target the old
laptop `pgdata`, never the new Docker volume. Preserve those files after cutover
until their retirement is explicitly authorized.

The platform stores state locally at `terraform/platform/terraform.tfstate`.
Legacy `terraform/argocd` state is preserved until transferred and archived privately.
State, state backups, plans, legacy generated manifests and real `.tfvars` are ignored;
state may contain Argo CD credentials and must be kept private. There is no AWS
provider or remote AWS backend. Back up state privately and never reuse AWS state.

Both methods below remove the entire `argocd-postgres` cluster, including app
deployments, Argo CD, Traefik and all Kubernetes Secrets. **Deleting the cluster
does not delete the laptop database files.** Choose whether to reuse or remove
those files before registering apps again.

Do not use `terraform destroy`: kind owns the cluster, and the Terraform-managed
Helm releases have `prevent_destroy`. Keep the platform state for this same
named cluster; the next apply refreshes it and recreates the missing releases.
The teardown helper below deletes a selected kind cluster only after explicit
confirmation. By default it keeps laptop database files; `--delete-database`
also discards myfastapiapp's data. It keeps local configuration and Terraform
state, and never runs `terraform destroy` or prunes Docker resources.

### Scripted teardown (keep database files)

Complete the database backup, restore verification and writer shutdown in
[preparation](docs/rebuild.md#prepare-for-either-method), then run from this repository:

```sh
./scripts/teardown-kind.sh --dry-run
./scripts/teardown-kind.sh
```

The preview makes no changes. The second command asks you to type
`delete argocd-postgres`, saves cluster Secrets and both local Terraform states
privately under `$HOME/.local/share/infra-local/backups/`, pauses Argo CD and
refuses to proceed while syncs or Jobs are active. It drains all kind nodes
before deleting the cluster, allowing PostgreSQL's normal shutdown grace period.
It does not create or test database backups. Existing filled Secret files and
backup archives are retained. The saved Secrets contain credentials; keep them private.

An optional cluster argument selects another local kind cluster, for example
`./scripts/teardown-kind.sh example --dry-run`. The script checks that the explicit
`kind-<cluster>` context's node names match Docker's kind nodes. Use
`INFRA_LOCAL_KUBECONFIG` for one other absolute kubeconfig file. An already absent
cluster is a successful no-op. If shutdown fails, deletion stops; follow
[interrupted teardown](docs/rebuild.md#interrupted-teardown).

Continue with [startup](#spin-up-after-either-method). For an empty database,
use the optional argument below or the app-owned physical removal procedure
in method 2 after teardown.

### Scripted teardown including myfastapiapp's database

To test a fresh database as well as a fresh cluster:

```sh
./scripts/teardown-kind.sh --delete-database --dry-run
./scripts/teardown-kind.sh --delete-database
```

The script displays the exact host `pgdata` path discovered from the running
kind nodes' Docker mounts, then requires `delete argocd-postgres and database`.
After successful drain and cluster deletion, it delegates deletion to the
[app-owned helper](../gitops/apps/myfastapiapp/overlays/local/postgres/delete-data.py).
Only myfastapiapp's `pgdata` is removed: accounts, sessions, records and database
roles/passwords are lost. The app directory, other apps' data, filled Secret
files, backups and Terraform state are kept. Retain a verified database backup
first if you may need this data later.

Python 3 and the sibling `gitops` checkout are required for this argument. The
helper rejects unsafe/ambiguous paths, symlinked app/data directories, and
storage mounted by other running containers. Without a live cluster, the
wrapper cannot discover the original path and stops; see
[interrupted teardown](docs/rebuild.md#interrupted-teardown) for cleanup retries.

### Method 1: keep the database

1. Follow [preparation and clean shutdown](docs/rebuild.md#prepare-for-either-method):
   pause root/app auto-sync, finish migrations, stop writers, back up and verify
   restoration, then stop PostgreSQL cleanly. Save original database credentials
   and privately back up Terraform state.
2. Delete the cluster:

   ```sh
   kind delete cluster --name argocd-postgres
   ```

3. Leave the laptop storage directory and `kind/cluster.local.yaml` intact.
   Follow [startup below](#spin-up-after-either-method) using the same database
   password, storage mount and compatible PostgreSQL major version.

### Method 2: destroy the database

1. Follow the same [preparation and clean shutdown](docs/rebuild.md#prepare-for-either-method).
   Confirm that you intend to discard the app's data; keep a verified backup if
   you may need it later.
2. Delete the cluster:

   ```sh
   kind delete cluster --name argocd-postgres
   ```

3. Follow the app-owned [physical database removal instructions](../gitops/apps/myfastapiapp/overlays/local/postgres/reset.md#discard-the-database-after-cluster-teardown).
   Remove only the selected app's `pgdata` directory under the configured
   laptop storage parent. Keep the parent and other apps' data intact. Repeat
   each app's own removal procedure if discarding multiple databases.
4. Follow [startup below](#spin-up-after-either-method). PostgreSQL initializes
   an empty database and the app's migration Job creates its schema. Old
   accounts and records are gone; matching new database credentials may be used
   in both app Secrets. Existing backup archives are not deleted by this method.

### Spin up after either method

Start Docker Desktop, check that port 80 is available, and review
`kind/cluster.local.yaml`: the absolute laptop storage parent must mount at
`/mnt`, with the storage node label and port mapping from the example. From
`infra-local`:

```sh
./scripts/create-cluster.sh
./scripts/bootstrap-kind.sh
```

Review and approve the combined Terraform apply for Argo CD and Traefik. If
bootstrap reports legacy ownership, follow [adoption](docs/terraform.md#adopt-an-existing-installation)
before continuing; do not delete state to bypass it.

Before registering apps, reconnect the deployment Git credential in Argo CD,
recreate app/database and registry Secrets, and make the selected release images
available. Images loaded into the old kind cluster must be loaded again. Follow
[deployment setup](../gitops/docs/local/setup.md), then from `gitops` run:

```sh
./scripts/bootstrap.sh --environment local --context kind-argocd-postgres
```

Argo CD deploys GitHub `main`, including automatic migrations. Root **Synced**
only confirms registration; wait for each app's **Synced / Healthy** status and
follow its [verification steps](../gitops/apps/myfastapiapp/overlays/local/README.md#verify-the-deployment).
For retained data, test existing records and login before resuming traffic.
See [the full rebuild runbook](docs/rebuild.md) for shutdown and recovery commands.

References: [kind configuration](https://kind.sigs.k8s.io/docs/user/configuration/),
[Argo CD declarative setup](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/).
