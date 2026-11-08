# Terraform for the local platform

kind owns the cluster and host port mapping. One local Terraform root,
`terraform/platform`, owns Helm releases `argocd/argocd` and `traefik/traefik`.
Argo CD owns app resources from `gitops`; Compose owns the separate database.
Terraform reads this checkout and uses local state, with no AWS provider or
backend. See [prerequisites](../README.md#prerequisites) for required tools.

## Shared versions and upgrades

Both infrastructure repositories select Argo CD chart `10.11.6`, with default
images Argo CD `v3.5.6`, Dex `v2.46.1` and Redis `8.6.4-alpine`. See the chart's
[versioned values](https://github.com/argoproj/argo-helm/blob/argo-cd-10.11.6/charts/argo-cd/values.yaml).
Local uses `application.instanceLabelKey: app.kubernetes.io/instance` for app
tracking. Traefik chart `42.0.0` provides standard Ingress support, class
`traefik`, NodePort `30080`, with dashboard/HTTPS exposure and Traefik CRDs
disabled. The Helm provider is pinned to `3.3.0`.

Review the full plan before changing charts or values. Terraform applies
controller upgrades independently of published GitOps changes. Verify all
controller rollouts, admin login, Git access, app registrations, sync policies,
endpoints and retained app data afterwards. Review upstream chart changes and
rendered resources as part of an upgrade.

## Inspect and apply manually

Defaults are cluster `deployment`, context `kind-deployment`. Use one cluster
per checkout/state. Providers explicitly use `kind-<cluster_name>` and one
`kubeconfig_path`. Configure another cluster/file in ignored
`terraform/platform/terraform.tfvars` using its example.

Run from `infra-local` after creating kind:

```sh
umask 077
terraform -chdir=terraform/platform init -lockfile=readonly
terraform -chdir=terraform/platform plan -out=platform.tfplan
terraform -chdir=terraform/platform show platform.tfplan
terraform -chdir=terraform/platform apply platform.tfplan
./scripts/setup-ingress-kind.sh
```

A fresh plan creates two Helm releases. `scripts/bootstrap-kind.sh` performs
one interactive apply, then checks CRDs and both controllers. It refuses
untracked installations before applying. Helm plans do not show a full
Kubernetes object diff; render both charts with `scripts/validate.sh` and
inspect `helm template` output when reviewing changes.

## Maintain provider checksums

The committed `terraform/platform/.terraform.lock.hcl` contains checksums for
Apple Silicon (`darwin_arm64`) and GitHub Actions (`linux_amd64`). After changing
a provider pin, regenerate both from `infra-local`:

```sh
terraform -chdir=terraform/platform providers lock \
  -platform=darwin_arm64 -platform=linux_amd64
```

Review and commit the lock file with the provider version change. This fetches
checksums without applying infrastructure or changing state. Keep
`init -lockfile=readonly` in validation and bootstrap. A lock with only the
macOS package hash can fail provider validation on Linux.
See [Terraform's provider lock command](https://developer.hashicorp.com/terraform/cli/commands/providers/lock).

## Recover platform state

State, backups, plans and real `.tfvars` are ignored and may contain credentials.
Keep private backups with `umask 077`. Never overwrite newer state with a saved
snapshot or reuse another cluster's state.

If state is lost while the cluster survives, inspect the actual Helm releases
and restore their bindings before applying. Use the selected kubeconfig/context:

```sh
helm --kubeconfig "$HOME/.kube/config" --kube-context kind-deployment -n argocd list --all
helm --kubeconfig "$HOME/.kube/config" --kube-context kind-deployment -n traefik list --all
terraform -chdir=terraform/platform init -lockfile=readonly
terraform -chdir=terraform/platform import helm_release.argocd argocd/argocd
terraform -chdir=terraform/platform import helm_release.traefik traefik/traefik
```

Import only verified releases absent from state. Match their pinned charts and
values, then review a fresh full plan before applying. An import records an
existing release; it does not install a controller. Use [cluster rebuild](rebuild.md)
for deliberate replacement of the same named kind cluster.

## Safeguards and validation limits

Both releases have `prevent_destroy`, disable forced replacement and automatic
ownership takeover, and use atomic rollback for upgrades. These settings do not
protect against removing configuration, internal chart changes or manual cluster
deletion. Keep independent database and credential backups.

Validation uses temporary copies and backend-disabled initialization with
committed locks. It renders charts, checks schemas, Terraform, Compose and Bash,
and runs database/bootstrap/teardown safety tests. It needs internet and the
[documented tools](../README.md#prerequisites), but no running cluster or AWS
credentials. It does not validate kind YAML, CRD semantics, live plans,
credentials, application data or runtime health.
