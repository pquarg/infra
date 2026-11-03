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
    kind/cluster.example.yaml   # Cluster, localhost port and parent storage mount
    argocd/install/              # Argo CD v3.5.5 and its namespace
    argocd/traefik.yaml          # Shared Traefik Helm Application, chart 42.0.0
    scripts/                    # Create, bootstrap and validate the platform
    docs/rebuild.md              # Preserve data across a planned rebuild
  deploy-local/                 # App discovery, manifests and releases
  apps/myfastapiapp/             # Application code and image build
```

Platform setup reads this checkout directly. It does not require an
`infra-local` GitHub repository or Git credential. Once registered, Argo CD
reconciles Traefik from its pinned Helm chart. After changing platform files,
validate and explicitly reapply them; this repository has no Git-watching root
Application. `deploy-local` supplies its own root Application for app discovery.

## Prerequisites

Install Docker Desktop (and start it), kind, kubectl and Python 3 for the app
tooling. The existing deployment validation targets Kubernetes `1.36.0`,
kubectl `v1.36.1`, kind `v0.33.0`; the cluster example uses kind's default node
image. For schema validation also install kubeconform `v0.8.0`. On macOS,
Homebrew supplies kind, kubectl and kubeconform. Internet access is required to
download images, the pinned Argo CD manifest, Helm charts and validation schemas.

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
and pass the same cluster name to the platform scripts. App runbooks and Argo CD
UI commands below use the default; adjust their explicit contexts consistently.

## 2. Install Argo CD and shared ingress

```sh
./scripts/bootstrap-kind.sh
```

This applies the pinned Argo CD installation with server-side apply, waits for
its controllers, registers `Application/traefik`, and verifies the kind port
mapping and controller readiness. No app is registered by this step. The
platform scripts always use an explicit `kind-<cluster>` context. On an existing
installation, review version differences before rerunning bootstrap: it applies
the versions pinned here and can change installed components.

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

Validation renders the pinned Argo CD install and Traefik Application, checks
Kubernetes/Argo CD schemas and Bash syntax, and needs no running cluster. The
three upstream CRD definitions are explicitly skipped because the schema
registry has no `CustomResourceDefinition` schema; the remaining installation
resources and our Traefik Application are checked strictly. Unknown platform
resource kinds still fail. CI runs the same checks. kind configuration is
consumed by cluster creation; these checks do not validate kind YAML, CRD
definition semantics or runtime readiness.

After reviewing a Traefik configuration change, apply only its registration:

```sh
kubectl --context kind-argocd-postgres apply -k argocd
./scripts/setup-ingress-kind.sh
```

Argo CD reconciles the Helm release. Argo CD upgrades use a reviewed version
change under `argocd/install/` and the platform bootstrap. Cluster port or mount
changes require a [planned rebuild](docs/rebuild.md); editing YAML cannot update
an existing kind node's mounts or published ports.

The moved `kind/postgres-config.local.yaml`, if present in this checkout, is
the previous machine-specific configuration. It remains ignored and is not
automatically selected by the scripts. Review it before reuse; do not replace
an existing cluster or relocate data merely to match the new example.

## Recovery and removal

Follow [the rebuild guide](docs/rebuild.md) before deleting a cluster containing
data. Laptop database directories survive cluster deletion, but Kubernetes
objects and credentials do not. Backups and tested restoration remain app-owned.
No script here automatically deletes clusters, storage or credentials.

References: [kind configuration](https://kind.sigs.k8s.io/docs/user/configuration/),
[Argo CD declarative setup](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/).
