# Local infrastructure

This independent repository provisions the local platform used by
[`gitops`](../gitops/README.md): a kind cluster, Argo CD and shared Traefik
installed through Terraform Helm releases, and a separate Compose PostgreSQL
database. Application source and image builds live in `../apps/`; application
manifests, connection Secrets and schema migration Jobs live in `gitops`.

Run commands on this page from `infra-local` unless a step changes directory.
Complete the database setup, platform setup and GitOps app setup in that order.
An accessible Argo CD UI confirms the controller is running; the app still
needs its database and credentials before it can start.

```text
infra-local/
  kind/cluster.example.yaml    # Cluster and localhost ingress port
  database/                    # Separate deployment PostgreSQL Compose service
  argocd/                      # Pinned Helm chart and values
  traefik/                     # Pinned Helm chart and values
  terraform/platform/          # One local state for both controller releases
  scripts/                     # Create, bootstrap, readiness and validation
  docs/database.md             # Database setup, credentials, backup and restore
  docs/terraform.md            # Platform plans, state recovery and upgrades
  docs/rebuild.md              # Cluster teardown and rebuild
```

Terraform reads this checkout directly and owns the shared controllers. Argo CD
reads published GitOps configuration and owns app resources. Compose owns the
database independently of kind and Terraform. Each resource has one owner.

## Prerequisites

Install and start Docker Desktop. Install kind `v0.33.0`, kubectl `v1.36.1` and
Terraform `1.17.x` (CI uses `1.17.1`). The kind example pins Kubernetes `1.36.4`
by image digest, sharing EKS's configured `1.36` minor version.
See the [kind release images](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0).

Validation also needs Helm 3 (CI uses `v3.19.0`), kubeconform `v0.8.0` and
Docker Compose. It validates Compose configuration without a running daemon.
Internet access is required for pinned providers, images, charts and schemas.
Ports `127.0.0.1:80` and `127.0.0.1:5433` must be available.
The database uses Docker Desktop host routing; see [database setup](docs/database.md).

## 1. Prepare the database and cluster

Follow [database setup](docs/database.md#first-setup) to configure private
password files and start PostgreSQL with its external Docker volume. This is
a required step: `bootstrap-kind.sh` installs controllers but does not start
PostgreSQL or generate database passwords. Confirm the database is healthy:

```sh
./scripts/local-database.sh status
```

Expect the `postgres` service to be running and healthy on port `5433`. If
`database/.env` or the password files are missing, finish
[database first setup](docs/database.md#first-setup) before continuing.
Then create the ignored kind configuration and cluster:

```sh
cp -n kind/cluster.example.yaml kind/cluster.local.yaml
./scripts/create-cluster.sh
```

The default cluster is `deployment`, context `kind-deployment`. No database
mount or storage node label is required. The script refuses to replace an
existing cluster; skip creation when the cluster already exists.

The image pin takes effect only during cluster creation. Editing the example
or ignored configuration does not upgrade a running cluster or change its
ports. Cluster replacement follows the [rebuild procedure](docs/rebuild.md).

To select another name/config, use `./scripts/create-cluster.sh another-cluster
/absolute/config.yaml` and pass that name to platform scripts. Use one cluster
per Terraform checkout/state. Set `cluster_name` and `kubeconfig_path` in ignored
`terraform/platform/terraform.tfvars` for manual Terraform commands; its example
shows the available settings.

## 2. Install Argo CD and shared ingress

```sh
./scripts/bootstrap-kind.sh
```

Bootstrap initializes `terraform/platform`, asks you to review and approve one
apply for both pinned Helm releases, then verifies Argo CD CRDs/controllers,
Traefik, its IngressClass/NodePort and the kind host port mapping. It registers
no app. It refuses existing controllers or Helm records absent from its state;
[state recovery](docs/terraform.md#recover-platform-state) covers lost bindings.

The scripts and provider explicitly select `kind-<cluster>` from
`~/.kube/config`. Set `INFRA_LOCAL_KUBECONFIG` to one absolute file to select
another kubeconfig. Bootstrap's arguments override ignored Terraform variables.
For inspection without applying, use the [manual plan commands](docs/terraform.md#inspect-and-apply-manually).

To open the Argo CD UI, leave this running in another terminal:

```sh
kubectl --context kind-deployment -n argocd \
  port-forward service/argocd-server 8080:443
```

Open https://localhost:8080 and sign in as `admin`. Retrieve the initial password
on a fresh installation:

```sh
kubectl --context kind-deployment -n argocd \
  get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
printf '\n'
```

Use the replacement password if it was changed. Under **Settings → Repositories**,
connect `git@github.com:pquarg/gitops.git` with a read-only Git credential usable
by the `default` project. Keep the credential outside Git. If using another
remote, update all `repoURL` values in `gitops/root/staging.yaml` and
`gitops/argocd/local/apps.yaml`. Git authentication and app image-pull
credentials are separate.

## 3. Hand over to gitops

The platform provides:

| Setting | Value |
| --- | --- |
| Default context | `kind-deployment` |
| Argo CD namespace | `argocd`, with Application/ApplicationSet CRDs ready |
| Shared ingress | Namespace `traefik`, IngressClass `traefik` |
| HTTP mapping | `127.0.0.1:80` → node `30080` → Traefik |
| Deployment database | Compose PostgreSQL at `host.docker.internal:5433` from kind Pods |
| Database persistence | External Docker volume `deployment-project-myfastapiapp-postgres` |

Follow [local deployment setup](../gitops/docs/local/setup.md) to provision the
app namespace, connection and registry Secrets, selected image and Git access.
Complete its app preparation before running the GitOps bootstrap. For
`myfastapiapp`, all of these must be ready:

- Compose PostgreSQL is healthy and the existing app password is available.
- Argo CD can read the deployment Git repository.
- Namespace `myfastapiapp` contains `registry-credentials` for private GHCR
  pulls and `myfastapiapp-secrets` for the database connection.
- The connection uses `host.docker.internal:5433`, and the Pod database probe
  reports `Database connectivity, app privileges and transactional DDL passed.`

The [GitOps setup guide](../gitops/docs/local/setup.md#4-validate-publish-and-register-staging)
contains the validation, publication and registration commands to run next.

The `root` Application discovers `apps/*/overlays/local` through its
ApplicationSet. App sync is automatic, including schema migrations, so the
database and credentials must be ready first. Root **Synced** confirms
registration; check each app's **Synced / Healthy** status and verification
procedure before claiming deployment success.
If sync fails, use the app's [first-sync troubleshooting](../gitops/apps/myfastapiapp/overlays/local/README.md#troubleshoot-the-first-sync).

## Validate and update

```sh
./scripts/validate.sh
git diff --check
```

Validation renders the pinned Argo CD and Traefik charts, checks Terraform
formatting/configuration, Compose configuration, database and bootstrap/teardown
safety tests, Kubernetes schemas and Bash syntax. It needs no running cluster
or AWS credentials. Initialization uses temporary configuration copies,
`-backend=false` and committed provider locks, leaving working state, variables
and provider directories untouched. The chart's three upstream CRD definitions
are skipped because the schema registry has no `CustomResourceDefinition`
schema; other unknown resource kinds fail. CI runs the same checks.
These checks do not validate kind YAML, CRD semantics or runtime readiness.

With Docker Desktop running, `bash tests/database-integration.sh` separately
tests database privileges, host routing, dump/restore ownership and external
volume retention using a temporary project and volume. It cleans up its own
resources and does not start the deployment database or create a cluster.

For chart, values or provider changes, follow the
[Terraform guide](docs/terraform.md), review the full platform plan and apply it
separately from app GitOps. Preserve pins and committed provider locks; refresh
checksums for both macOS ARM64 and Linux AMD64 when changing provider versions.

## Tear down and spin up again

Follow [cluster rebuild](docs/rebuild.md). Preview with:

```sh
./scripts/teardown-kind.sh --dry-run
```

Teardown requires typed confirmation, privately backs up cluster Secrets and
platform state, pauses Argo CD, checks for active syncs/Jobs, drains all nodes
and deletes only the selected kind cluster. It preserves Compose PostgreSQL,
its external volume, local configuration, Terraform state and backups.
Database backup, restore and deliberate reset are [separate procedures](docs/database.md).

Platform state lives at `terraform/platform/terraform.tfstate` and may contain
credentials. Keep it and its backups private. Use separate state/checkouts for
different cluster names. Do not use `terraform destroy` for cluster replacement:
kind owns the cluster and both Helm releases have `prevent_destroy`.

References: [kind configuration](https://kind.sigs.k8s.io/docs/user/configuration/),
[Argo CD declarative setup](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/).
