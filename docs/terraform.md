# Terraform for the local platform

Terraform installs Argo CD as a Helm release, like `infra-aws`. This replaces the
upstream-manifest renderer and per-object imports; no Python is required.
Chart `10.11.0` packages Argo CD `v3.5.5`, preserving the previous Argo CD version.
The chart pin lives in `argocd/chart-version.txt`; settings live in
`argocd/values.yaml`. Terraform reads both files directly. The values also retain
the former Dex `v2.45.1` and Redis `8.2.3-alpine` images instead of upgrading to
the chart defaults during this ownership change.

## Owners and stages

| Owner | Resources |
| --- | --- |
| kind and `scripts/create-cluster.sh` | Cluster, node, host ports and storage parent mount |
| `terraform/argocd` | Helm release `argocd` in namespace `argocd`, including CRDs |
| `terraform/platform` | `Application/traefik` in namespace `argocd` |
| Argo CD | Traefik chart resources and app registrations/workloads from `deploy-local` |
| `deploy-local` | App manifests, releases, databases, PV/PVCs, credentials, backups and recovery |

kind remains outside Terraform: it creates a Kubernetes API that the Helm and
Kubernetes providers can connect to. The two Terraform roots remain sequential
because the Kubernetes provider queries resource types during planning. It cannot
plan an Application before Argo CD's CRD is installed. The bootstrap wrapper
waits for Argo CD between applies and checks Traefik readiness afterwards.

There is no `local-exec`, AWS provider/backend or platform Git-watching root
Application. Terraform reads this checkout. Argo CD reads Traefik's chart
repository; the separate deployment root reads app manifests from Git.

## Inspect and apply manually

Run from `infra-local`. Install the README's prerequisites and create the kind
cluster first. For an existing installation, complete adoption below first.

```sh
umask 077
terraform -chdir=terraform/argocd init
terraform -chdir=terraform/argocd plan -out=argocd.tfplan
terraform -chdir=terraform/argocd show argocd.tfplan
```

On a fresh cluster, expect one Helm release creation. On an adopted installation,
investigate unexpected changes before applying. Helm plans show chart settings,
not a complete Kubernetes object diff. Render the pinned chart for inspection:

```sh
helm template argocd argo-cd \
  --repo https://argoproj.github.io/argo-helm \
  --version "$(cat argocd/chart-version.txt)" \
  --namespace argocd --values argocd/values.yaml --include-crds
```

When ready, apply and wait before planning the second root:

```sh
terraform -chdir=terraform/argocd apply argocd.tfplan
kubectl --context kind-argocd-postgres -n argocd wait --for=condition=Established \
  crd/applications.argoproj.io crd/applicationsets.argoproj.io --timeout=120s
kubectl --context kind-argocd-postgres -n argocd \
  rollout status statefulset/argocd-application-controller --timeout=300s
kubectl --context kind-argocd-postgres -n argocd \
  rollout status deployment/argocd-server --timeout=300s
kubectl --context kind-argocd-postgres -n argocd \
  rollout status deployment/argocd-repo-server --timeout=300s
kubectl --context kind-argocd-postgres -n argocd \
  rollout status deployment/argocd-applicationset-controller --timeout=300s

terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform plan -out=platform.tfplan
terraform -chdir=terraform/platform show platform.tfplan
terraform -chdir=terraform/platform apply platform.tfplan
./scripts/setup-ingress-kind.sh
```

`./scripts/bootstrap-kind.sh` performs these stages with interactive Terraform
approval. It needs neither Python nor the Helm CLI and does not auto-approve.
App credentials and registration still follow `deploy-local` setup afterwards.

## Adopt an existing installation

Do not rebuild the cluster, delete its namespace/CRDs, reset passwords or move
database files. Bootstrap stops on legacy manifest state or an existing untracked
Argo CD server. Normal Terraform applies do not enable Helm `take_ownership` or
forced resource replacement. Adoption is a separate operator procedure.

Use one cluster per checkout/state. The commands below select the default cluster;
adjust the context, kubeconfig and both roots' ignored variables consistently for
another cluster. First inspect whether Helm already manages Argo CD:

```sh
helm --kubeconfig "$HOME/.kube/config" --kube-context kind-argocd-postgres \
  -n argocd list --all
```

### An existing Helm release

If `argocd` is already a Helm release in namespace `argocd`, inspect its chart,
values and status with `helm status` and `helm get values` using the same explicit
kubeconfig/context. Preserve intended customizations in `argocd/values.yaml`
without adding credentials. If legacy per-object bindings remain in state, back
up and forget those bindings as described below before importing the release.

```sh
umask 077
terraform -chdir=terraform/argocd init
terraform -chdir=terraform/argocd import helm_release.argocd argocd/argocd
```

Skip import if `terraform state list` already includes `helm_release.argocd`.
Import records an existing release; it does not convert a manifest installation.
Review a new plan before applying; imported provider settings can differ from
the configuration even when the chart version is unchanged.

### A manifest-based Argo CD installation

This is a one-time ownership change with Argo CD downtime, not a routine
bootstrap. It requires Helm CLI 3.19.0 or compatible Helm 3 with `--take-ownership`.
Review the following procedure before executing it. Recreating controller
workloads is a deliberate live change requiring authorization for that scope.
The app/database workloads and Traefik stay running, but Git reconciliation
pauses while Argo CD is unavailable. This migration is not exercised by the
cluster-free validator.

1. Complete any previous deployment-repository ownership transfer so its root
   no longer owns Traefik. Pause root/app auto-sync, let active syncs/migrations
   finish, and record the policies to restore. Follow each app's backup runbook.
2. Privately back up both local state files and the Argo CD namespace's
   Applications, ApplicationSets, AppProjects, ConfigMaps and Secrets. Use an
   absolute backup directory outside Git and `umask 077`. Keep the current
   installation configuration/revision available for recovery. Never print or
   publish the backed-up Secret data. For example, after setting `backup_dir` to
   that private directory:

   ```sh
   umask 077
   mkdir -p "$backup_dir"
   kube=(kubectl "--kubeconfig=$HOME/.kube/config" --context=kind-argocd-postgres)
   "${kube[@]}" -n argocd get applications,applicationsets,appprojects,configmaps,secrets \
     -o yaml > "$backup_dir/argocd-resources.yaml"
   ```

   Copy each existing `terraform/<stage>/terraform.tfstate` and its backups into
   that directory before changing ownership. Skip absent state files. Record
   `argocd-secret`, `argocd-redis` and `argocd-notifications-secret` data privately
   so you can compare them afterwards. Preserve Git repository credentials and
   the changed admin password; do not replace them with fresh-install values.
3. Render the chart using the command above into a private file and compare it
   with the existing installation. Transfer custom ConfigMap settings, RBAC,
   known hosts and certificates into the corresponding chart values first.
   Keep `configs.secret` credential values unset: the chart's default
   `argocd-secret` template omits data when none is supplied, leaving Argo CD's
   existing password, signing key and TLS data intact. Git credential Secrets
   are not declared by this chart configuration. Retain the checked-in tracking
   label setting so existing apps keep their tracking convention.
4. Compare workload selectors before adoption. The upstream manifests select
   most controllers only by name; the Helm chart also selects by release
   instance. Kubernetes cannot patch those immutable selectors. Once the
   migration and downtime are approved, recreate only the Argo CD controllers
   with changed selectors. For the former pinned installation, these are:

   ```sh
   "${kube[@]}" -n argocd delete deployment \
     argocd-server argocd-repo-server argocd-dex-server \
     argocd-applicationset-controller argocd-notifications-controller
   "${kube[@]}" -n argocd delete statefulset argocd-application-controller
   ```

   Do not delete Secrets, CRDs, Applications, the namespace, app controllers,
   databases or storage. The Redis selector is unchanged in this pinned chart.
5. Adopt the remaining installation objects and create the chart's controllers:

   ```sh
   helm --kubeconfig "$HOME/.kube/config" --kube-context kind-argocd-postgres \
     upgrade --install argocd argo-cd \
     --repo https://argoproj.github.io/argo-helm \
     --version "$(cat argocd/chart-version.txt)" \
     --namespace argocd --values argocd/values.yaml \
     --take-ownership --wait --wait-for-jobs --timeout 10m
   ```

   Do not add `--atomic`, `--cleanup-on-fail` or `--force` to this adoption.
   An automatic uninstall after a failed first install could delete adopted
   resources. If adoption fails, retain the release and repair it in place;
   do not uninstall it or run the old installer concurrently. Retain all private
   backups and inspect the Helm status/events before continuing.
6. Verify controller readiness, the unchanged credential data, admin sign-in,
   Git repository access, Application registrations and app health. Compare the
   old and chart resource inventories; upstream-only metrics Services or other
   objects may remain. Keep them until reviewed separately, rather than deleting
   everything carrying an Argo CD label. Do not resume auto-sync yet.
7. From the configuration-free `infra-local` root, list the old bindings with
   `terraform state list -state=terraform/argocd/terraform.tfstate`. This reads
   local state without initializing obsolete providers. For **only the groups
   present**, use `terraform state rm -state=terraform/argocd/terraform.tfstate
   -dry-run ADDRESS` to review, then repeat without `-dry-run` to forget them.
   The possible addresses are:

   ```text
   kubernetes_manifest.namespace
   kubernetes_manifest.installation
   kubectl_manifest.installation
   kubernetes_secret_v1.installation
   ```

   Forgetting a group removes its state bindings, not its live objects. Keep the platform root's state and
   `kubernetes_manifest.traefik` binding. Do not apply between forgetting old
   bindings and importing the Helm release. `migration.tf` also contains
   `removed` blocks with `destroy = false` so old bindings cannot become deletion
   plans just because their resource blocks were retired.
8. Initialize and import `helm_release.argocd` using the Helm-release commands
   above. Review a new plan and apply only expected changes. Once the release is
   imported, routine applies use `atomic = true` for rollback on upgrade failure.
   Verify platform/app health again, then restore the recorded auto-sync policies.

### Import Traefik's registration if state is missing

If the Traefik Application already exists but is absent from platform state:

```sh
terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform import kubernetes_manifest.traefik \
  'apiVersion=argoproj.io/v1alpha1,kind=Application,namespace=argocd,name=traefik'
```

Only import the Application, never Traefik's chart workloads. Skip this step if
the binding already exists. Review both roots' plans before running bootstrap.

## Local configuration and state

Both roots default to context `kind-argocd-postgres` in `~/.kube/config`.
They do not use the current kubectl context or an ambient `KUBECONFIG` list.
For manual Terraform commands, optionally copy each root's
`terraform.tfvars.example` to its ignored `terraform.tfvars`. Set matching
`cluster_name` and `kubeconfig_path` in both. The bootstrap wrapper takes a cluster
argument and uses `INFRA_LOCAL_KUBECONFIG` for another kubeconfig:

```sh
INFRA_LOCAL_KUBECONFIG=/absolute/kubeconfig ./scripts/bootstrap-kind.sh another-cluster
```

Each root uses Terraform's local backend and its own `terraform.tfstate`,
`.terraform/` directory and committed `.terraform.lock.hcl`. Do not copy AWS state
or backend settings here. Use one cluster per checkout and keep state/private
backups outside Git. State and binary plans may contain credentials, including
Helm release information. The wrapper uses `umask 077`; use it for manual commands
too. Never edit state JSON manually or reuse old saved plans after migration.

If state is lost while the cluster survives, import the Helm release and Traefik
registration before applying. A fresh clone does not inherit local state.
For a deliberately rebuilt cluster with the same name and Helm-based state,
Terraform refreshes and recreates missing resources; install Argo CD before
planning platform again. See [rebuild and recovery](rebuild.md).

Managed resources have `prevent_destroy`. It blocks routine Terraform replacement
and destroy plans, but does not protect against resource-block removal, Helm's
internal upgrade operations or manual `kubectl`/kind deletion. CRDs also have
Helm's keep policy. These guards are not backups or authorization to destroy.

## Validation limits

`./scripts/validate.sh` checks Terraform formatting/configuration in temporary
copies with backend initialization disabled, runs Bash bootstrap tests, checks
Bash syntax and validates rendered Helm/Kustomize resources against schemas.
The pin file is shared by Terraform and Helm rendering so versions cannot drift.
Helm 3 and kubeconform are additional validation tools, not bootstrap dependencies.

Validation requires network access but no cluster or AWS credentials. It does
not validate kind configuration, CRD definition semantics, live provider plans,
Helm adoption/upgrade behavior, credential retention or workload readiness.
The three upstream CRD definitions are explicitly skipped by schema validation;
unknown platform resource kinds still fail.

References: [Pinned chart metadata](https://github.com/argoproj/argo-helm/blob/argo-cd-10.11.0/charts/argo-cd/Chart.yaml),
[chart values](https://github.com/argoproj/argo-helm/blob/argo-cd-10.11.0/charts/argo-cd/values.yaml),
[Helm provider release settings/import](https://github.com/hashicorp/terraform-provider-helm/blob/v3.3.0/docs/resources/release.md),
[Terraform removed blocks](https://developer.hashicorp.com/terraform/language/block/removed).
