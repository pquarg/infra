# Local infrastructure

Prepare the local Kubernetes platform used by the sibling `deploy-local`
repository. This is an independent Git repository. It owns the kind cluster,
host port and storage mappings, pinned Argo CD installation, and shared Traefik
controller. Application code lives in app repositories; app deployments,
PostgreSQL workloads, PV/PVCs, credentials, backups and migrations stay in
`deploy-local`.

```text
deployment-project/
  infra-local/                  # This repository
    kind/cluster.example.yaml    # Cluster, localhost port and parent storage mount
    argocd/chart-version.txt     # Helm chart 10.11.0 (Argo CD v3.5.5)
    argocd/values.yaml           # Argo CD settings
    argocd/traefik.yaml          # Shared Traefik Helm Application, chart 42.0.0
    terraform/argocd/            # Terraform root: Argo CD Helm release
    terraform/platform/         # Terraform root: Traefik Application registration
    scripts/                    # Create, bootstrap, readiness checks and validation
    docs/terraform.md            # Ownership, plans, state and existing-cluster adoption
    docs/rebuild.md              # Preserve data across a planned rebuild
  deploy-local/                 # App discovery, manifests and releases
  apps/myfastapiapp/             # Application code and image build
```

Platform setup reads this checkout directly. It does not require an
`infra-local` GitHub repository or Git credential. Once registered, Argo CD
reconciles Traefik from its pinned Helm chart. After changing platform files,
validate and explicitly plan/apply them with Terraform; this repository has no Git-watching root
Application. `deploy-local` supplies its own root Application for app discovery.

Terraform installs Argo CD through its Helm provider, following the same approach
as `infra-aws`. There is no Python rendering or import step. kind creates the
local cluster, ports and mounts; Terraform installs Argo CD and registers
Traefik; Argo CD installs and maintains Traefik from its chart. The two Terraform
stages remain separate because Argo CD's Application CRD must exist before the
Kubernetes provider can plan a Traefik Application. Read [the Terraform guide](docs/terraform.md)
for plans, state and adopting an existing installation without rebuilding it.

## Prerequisites

Install Docker Desktop (and start it), kind, kubectl and Terraform
`1.17.x` (CI uses `1.17.1`). The Argo CD root pins the Helm provider to `3.3.0`;
the platform root pins the Kubernetes provider to `3.4.0`. Both roots have
committed provider lock files. The existing deployment
validation targets Kubernetes `1.36.0`,
kubectl `v1.36.1`, kind `v0.33.0`; the cluster example uses kind's default node
image. For validation also install Helm 3 (CI uses `v3.19.0`) and kubeconform
`v0.8.0`. Helm CLI renders the chart for validation and is used for one-time
adoption; normal bootstrap uses Terraform's Helm provider without the Helm CLI.
Python is not required by this repository. Internet access is required to
download providers, images, pinned Helm charts and validation schemas.

Port `127.0.0.1:80` must be available. Docker Desktop must allow sharing the
chosen laptop storage directory. Existing clusters should follow
[the repository split instructions](../deploy-local/docs/repository-upgrade.md#split-local-infrastructure-from-app-deployment)
before adopting the new ownership; creating a second cluster is unnecessary.

## 1. Configure storage and create the cluster

Run from this repository root:

```sh
mkdir -p "$HOME/.local/share/staging/postgres/myfastapiapp-postgres"
cp -n kind/cluster.example.yaml kind/cluster.local.yaml
printf '%s/.local/share/staging/postgres\n' "$HOME"
```

Edit `kind/cluster.local.yaml`: replace `hostPath` with that absolute parent
directory. kind does not expand `$HOME` or `~` in YAML. The file is ignored by
Git. Preserve the node label, `/mnt` mount and localhost port mapping supplied
by the example. For each additional database, create `<app>-postgres/` under
the same parent before deploying it.

```sh
./scripts/create-cluster.sh
```

The default cluster is `argocd-postgres`, context `kind-argocd-postgres`. The
script refuses to replace an existing cluster. To choose another name or
config, use `./scripts/create-cluster.sh another-cluster /absolute/config.yaml`
and pass the same cluster name to the platform scripts. Each checkout's two
Terraform state files must belong to only one cluster; use separate checkouts
for different clusters. App runbooks and Argo CD
UI commands below use the default; adjust their explicit contexts consistently.

## 2. Install Argo CD and shared ingress

For an **existing Argo CD installation**, first follow
[adoption](docs/terraform.md#adopt-an-existing-installation). A manifest-based
installation needs a reviewed Helm ownership migration; importing a Helm release
alone cannot convert it. Bootstrap stops if it finds legacy manifest state or an
existing untracked Argo CD server. Do not rebuild the cluster to switch tools.
On a fresh cluster, skip adoption.

```sh
./scripts/bootstrap-kind.sh
```

This initializes the two independent local Terraform roots and asks you to
review and approve an Argo CD Helm apply. After its
CRDs and controllers are ready, it asks for a separate apply registering
`Application/traefik`, then verifies the kind port mapping and controller
readiness. Terraform needs the Application CRD to exist before it can plan that
registration. No app is registered by this step. The providers and scripts use
an explicit `kind-<cluster>` context from `~/.kube/config`, never the ambient
current-context. For another file, set `INFRA_LOCAL_KUBECONFIG` to its absolute
path. This also overrides the kubeconfig setting in both roots for the wrapper.
The wrapper's cluster argument likewise overrides their `cluster_name` values.
Review version differences before applying to an existing installation.

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
Repositories**, connect `git@github.com:pquarg/deploy-local.git` with a read-only
Git credential usable by the `default` project. Keep the credential outside Git.
If using another remote, change all `repoURL` values in `deploy-local`'s
`root/staging.yaml` and `argocd/apps.yaml` to match. Git authentication is
separate from the app's GHCR image-pull authentication.

## 3. Hand over to deploy-local

The platform provides this contract:

| Setting | Value |
| --- | --- |
| Default context | `kind-argocd-postgres` |
| Argo CD namespace | `argocd`, with Application/ApplicationSet CRDs ready |
| Shared ingress | Namespace `traefik`, IngressClass `traefik` |
| HTTP mapping | `127.0.0.1:80` → node `30080` → Traefik |
| Storage mount | Laptop parent directory → `/mnt` in the kind node |
| Storage node label | `staging.local/postgres-storage: "true"` |
| App database path | `/mnt/<app>-postgres`, with PV/PVC owned by the app |

From `deploy-local`, follow [deployment setup](../deploy-local/docs/setup.md):
provision the app's namespace, database and registry Secrets, make its selected
image available, validate and publish the deployment manifests, then run:

```sh
cd ../deploy-local
./scripts/bootstrap-kind.sh
```

That separate bootstrap registers `staging-root`; its ApplicationSet discovers
`apps/*` from GitHub `main`. App sync is automatic, including database creation
and migrations, so credentials and any retained data must be ready first. The
root no longer owns Traefik. Publishing app releases does not recreate the
cluster. Verify app readiness using its own deployment README.

## Validate and update

```sh
./scripts/validate.sh
git diff --check
```

Validation renders the pinned Argo CD chart and Traefik Application, checks
Terraform formatting/configuration, Bash bootstrap safety tests, Kubernetes/Argo CD
schemas and Bash syntax, and needs no running cluster or AWS credentials.
Terraform initialization uses temporary configuration copies, `-backend=false`
and the committed provider locks; it downloads providers without reading or
changing existing state, local variables or working provider directories. The
three upstream CRD definitions are explicitly skipped because the schema
registry has no `CustomResourceDefinition` schema; the remaining installation
resources (including Helm hook Jobs) and our Traefik Application are checked
strictly. Unknown platform resource kinds still fail. CI runs the same checks.
kind configuration is consumed by cluster creation; these checks do not validate kind YAML, CRD
definition semantics or runtime readiness.

After editing `argocd/traefik.yaml`, review and apply only its registration:

```sh
umask 077
terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform plan -out=platform.tfplan
terraform -chdir=terraform/platform show platform.tfplan
terraform -chdir=terraform/platform apply platform.tfplan
./scripts/setup-ingress-kind.sh
```

These manual examples use the default context; keep the two roots' ignored
configuration consistent if selecting another cluster or kubeconfig.
Argo CD reconciles the Helm chart. Do not also apply Terraform-owned objects
with `kubectl apply`. Argo CD upgrades use a reviewed version change in
`argocd/chart-version.txt`, validation and an apply of `terraform/argocd`.
Settings live in `argocd/values.yaml`; Terraform reads them directly.
The bootstrap wrapper also performs the Helm apply. Cluster port or mount
changes require a [planned rebuild](docs/rebuild.md); editing YAML cannot update
an existing kind node's mounts or published ports.

The moved `kind/postgres-config.local.yaml`, if present in this checkout, is
the previous machine-specific configuration. It remains ignored and is not
automatically selected by the scripts. Review it before reuse; do not replace
an existing cluster or relocate data merely to match the new example.

## Recovery and removal

Each Terraform root stores `terraform.tfstate` locally in its own directory.
State, state backups, plans, legacy generated manifests and real `.tfvars` are ignored;
state may contain Argo CD credentials and must be kept private. There is no AWS
provider or remote AWS backend. Back up state privately and never reuse AWS state.

Follow [the rebuild guide](docs/rebuild.md) before deleting a cluster containing
data. Laptop database directories survive cluster deletion, but Kubernetes
objects and credentials do not. Backups and tested restoration remain app-owned.
No script here automatically deletes clusters, storage or credentials.
Terraform-managed resources have `prevent_destroy`; routine destroy/replacement
plans are blocked. This is not a backup or protection from manual cluster deletion.

References: [kind configuration](https://kind.sigs.k8s.io/docs/user/configuration/),
[Argo CD declarative setup](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/).
