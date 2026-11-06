# Terraform for the local platform

kind owns the cluster, node, ports and storage mounts. One local Terraform root,
`terraform/platform`, owns real Helm releases `argocd/argocd` and `traefik/traefik`.
Argo CD owns application resources from `gitops`. Terraform reads this
checkout; there is no platform Git watcher, AWS provider/backend, Kubernetes
provider or local Python requirement.

Pins remain Argo CD chart `10.11.0` (Argo CD `v3.5.5`), Traefik `42.0.0`, Helm
provider `3.3.0`. Argo CD's Dex/Redis image overrides, Secret behavior and tracking
label are unchanged. Traefik retains standard Ingress support, class `traefik`,
NodePort `30080`, disabled dashboard/HTTPS exposure and disabled Traefik CRDs.

## Inspect and apply manually

Use one cluster per checkout/state. Providers always use `kind-<cluster_name>`
and the single `kubeconfig_path`, never the ambient context. Configure another
cluster/file in ignored `terraform/platform/terraform.tfvars` using its example.
Run from `infra-local` after creating kind or completing adoption below:

```sh
umask 077
terraform -chdir=terraform/platform init -lockfile=readonly
terraform -chdir=terraform/platform plan -out=platform.tfplan
terraform -chdir=terraform/platform show platform.tfplan
terraform -chdir=terraform/platform apply platform.tfplan
./scripts/setup-ingress-kind.sh
```

A fresh plan creates two Helm releases. The wrapper `scripts/bootstrap-kind.sh`
performs one interactive apply, then checks CRDs and both controllers. It also
checks legacy/unadopted ownership **before** any apply. Helm plans do not show a
full Kubernetes object diff; render both charts with `scripts/validate.sh` and
inspect `helm template` output when reviewing a migration.

## Maintain provider checksums

The committed `terraform/platform/.terraform.lock.hcl` includes provider package
checksums for both Apple Silicon development (`darwin_arm64`) and the GitHub
Actions runner (`linux_amd64`). After changing a provider pin, regenerate the
checksums for both platforms from `infra-local`:

```sh
terraform -chdir=terraform/platform providers lock \
  -platform=darwin_arm64 -platform=linux_amd64
```

Review and commit the generated lock file alongside the provider version change.
This command fetches checksums without applying infrastructure or changing state.
Keep `init -lockfile=readonly` in validation and bootstrap: it requires the
committed lock file to support the platform running Terraform. A lock file with
only the macOS package hash can let Linux initialization finish with a warning,
then make `terraform validate` fail with a provider checksum mismatch. The Linux
CI validation is the regression check for this failure.

See [Terraform's provider lock command](https://developer.hashicorp.com/terraform/cli/commands/providers/lock)
for additional target platforms and checksum verification.

## Adopt an existing installation

This is a separately scheduled live ownership migration, not part of ordinary
bootstrap. Freeze infrastructure applies/state writes, back up state and cluster
objects privately, record Git revisions and Helm/object identities, finish active
syncs/migrations, verify app backups/restoration, and record database/login/data,
localhost ingress and Git access baselines. Pause and record root/app sync policies.
Never delete the cluster, namespace/CRDs, credentials or database files to simplify it.
Use explicit context/kubeconfig on every Helm/kubectl command below.

Create a private transfer ledger with source/destination address, actual import
ID, state snapshot/serial and completed checkpoint. Inspect both old state files
from the configuration-free repository root:

```sh
terraform state list -state=terraform/argocd/terraform.tfstate
terraform state list -state=terraform/platform/terraform.tfstate
helm --kubeconfig "$HOME/.kube/config" --kube-context kind-argocd-postgres -n argocd list --all
helm --kubeconfig "$HOME/.kube/config" --kube-context kind-argocd-postgres -n traefik list --all
```

Skip absent state files. Preserve the final platform state at its existing path.
Do not run Terraform in the retired `terraform/argocd` directory; its ignored
state/local configuration is deliberately left untouched by the refactor.

### An existing Helm release

Inspect chart, values and status with `helm status` and `helm get values`. Preserve
custom settings in checked-in values without credentials. If Argo CD is tracked in
the old state, back it up, then forget only its release binding from the old state:

```sh
terraform state rm -state=terraform/argocd/terraform.tfstate -dry-run helm_release.argocd
terraform state rm -state=terraform/argocd/terraform.tfstate helm_release.argocd
terraform -chdir=terraform/platform init -lockfile=readonly
terraform -chdir=terraform/platform import helm_release.argocd argocd/argocd
```

Import only actual releases absent from the final state; skip an already-correct
binding. Imports record a release; they do not install Helm or convert manifest
objects. If import fails, repair it or restore the source binding under the freeze;
never apply while ownership is incomplete. Inspect imported provider defaults
before accepting a plan. Treat partial migration according to the ledger, not by
rerunning installation. A Git revert does not transfer state.

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

   Forgetting a group removes its state bindings, not its live objects. Keep the platform state; the Traefik binding is handled separately below. Do not apply between forgetting old
   bindings and importing the Helm release. `terraform/platform/migration.tf` also contains
   `removed` blocks with `destroy = false` so old bindings cannot become deletion
   plans just because their resource blocks were retired.
8. Import `helm_release.argocd` into **terraform/platform**, using the commands
   above. Continue with the Traefik transfer below before a combined apply.
   Restore auto-sync only after both controllers and app/data checks pass.

### Transfer Traefik from Argo CD to Helm

1. Privately save `Application/traefik`, its finalizers, sync policy and resource
   inventory, both state snapshots, and all Traefik object identities/selectors.
   Confirm the deployment root no longer declares Traefik. Disable auto-sync and
   wait for any operation to finish; inspect operation status rather than assuming
   disabling auto-sync stops an already-running sync.
2. Inspect finalizers. Only after reviewing the inventory, remove the Application
   registration **without cascading deletion**. These commands intentionally affect
   the registration alone; do not run an Argo CD cascade delete:

   ```sh
   kube=(kubectl "--kubeconfig=$HOME/.kube/config" --context=kind-argocd-postgres)
   "${kube[@]}" -n argocd patch application traefik --type=merge \
     -p '{"spec":{"syncPolicy":{"automated":null}}}'
   # Wait for operationState to be absent or completed, then inspect finalizers.
   "${kube[@]}" -n argocd get application traefik -o yaml
   "${kube[@]}" -n argocd patch application traefik --type=merge \
     -p '{"metadata":{"finalizers":null}}'
   "${kube[@]}" -n argocd delete application traefik --wait=true
   terraform state rm -state=terraform/platform/terraform.tfstate -dry-run kubernetes_manifest.traefik
   terraform state rm -state=terraform/platform/terraform.tfstate kubernetes_manifest.traefik
   ```

   Forget the binding only if present. If the Application is already gone, verify
   workloads remain and consult the ledger. Never apply the old root to detach it.
3. Argo CD normally renders Helm templates without creating a Helm release record.
   An import alone cannot adopt this installation. Render the exact chart with
   release name/namespace `traefik`, `--skip-crds` and `traefik/values.yaml`.
   Compare names, selectors, Services, class, RBAC and all values against the saved
   inventory. Review customizations and any restarts explicitly; stop on immutable
   selector differences instead of using force replacement.
4. If no actual release exists, adopt the running resources into Helm 3.19.0:

   ```sh
   helm pull traefik --repo https://traefik.github.io/charts \
     --version "$(cat traefik/chart-version.txt)" --destination "$backup_dir"
   helm --kubeconfig "$HOME/.kube/config" --kube-context kind-argocd-postgres \
     upgrade --install traefik "$backup_dir/traefik-$(cat traefik/chart-version.txt).tgz" \
     --namespace traefik \
     --values traefik/values.yaml --skip-crds --take-ownership \
     --wait --wait-for-jobs --timeout 10m
   ```

   Pulling the chart archive avoids Helm interpreting the local `traefik/`
   settings directory as a chart.

   Do not use `--atomic`, `--cleanup-on-fail`, `--force` or uninstall as rollback:
   a failed first adoption must retain its resources for repair. If a real release
   already exists, inspect and import it instead of reinstalling it.
5. Import the actual release into the final state:

   ```sh
   terraform -chdir=terraform/platform import helm_release.traefik traefik/traefik
   terraform -chdir=terraform/platform plan -out=platform.tfplan
   terraform -chdir=terraform/platform show platform.tfplan
   ```

   Expect both real release bindings and no remaining Application binding. The
   defensive `removed` blocks forget legacy bindings without deletion, but are
   not an adoption workflow. Ordinary bootstrap never enables takeover or force.
6. Check both controller rollouts/CRDs, Argo CD sign-in and Git access, existing
   app registrations, `scripts/setup-ingress-kind.sh`, localhost `/health` and
   `/ready`, existing database authentication and retained records. Restore
   recorded sync policies only when these pass. Review a fresh ordinary plan for
   unexpected resource changes. Archive old empty Argo CD state/config privately
   only after recovery is checked; keep the final platform state in place.

## Recovery and validation limits

Local state, backups, binary plans and real `.tfvars` remain ignored and may
contain credentials. Use `umask 077`; never edit state JSON or overwrite a newer
state with an old snapshot. If state is lost while the cluster survives, import
both actual releases before applying. See [rebuild](rebuild.md) for deliberate
cluster replacement using the same data and credentials.

Rollback depends on the checkpoint. Restore previous code and correct bindings
under the apply freeze, with one owner at a time. To return adopted Traefik to
Argo CD, detach its Terraform binding **without Helm uninstall**, then restore
its registration only after comparing the saved inventory. A Git revert alone
neither restores bindings nor undoes Helm adoption. Keep private backups until
all live checks pass.

Both Helm releases have `prevent_destroy`; CRDs retain their existing keep
policy. These guards do not protect against configuration removal, internal Helm
upgrade changes or manual cluster deletion. Normal upgrades use atomic rollback;
first adoption deliberately does not.

The validator uses temporary copies and backend-disabled init with committed
locks. It renders both charts, checks schemas/Bash and runs bootstrap safety tests.
It requires network access, Helm 3, Terraform, kubectl and kubeconform, but no
cluster, Python or AWS credentials. It does not validate kind YAML, CRD semantics,
live plans, Helm adoption, credentials, application data or runtime health.

References: [Helm 3 upgrade](https://v3.helm.sh/docs/helm/helm_upgrade/),
[Helm provider import](https://github.com/hashicorp/terraform-provider-helm/blob/v3.3.0/docs/resources/release.md),
[Terraform state rm](https://developer.hashicorp.com/terraform/cli/commands/state/rm),
[non-cascading Application deletion](https://argo-cd.readthedocs.io/en/stable/user-guide/app_deletion/).
