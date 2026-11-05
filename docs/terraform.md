# Terraform for the local platform

Terraform owns the same pinned platform objects that the former bootstrap
applied with kubectl. This adds reviewed plans and explicit state while keeping
the existing cluster, data paths, credentials and component versions. The Argo CD
installation stays on its upstream manifests rather than changing to a Helm
installation during the ownership transfer.

## Owners and stages

| Owner | Resources |
| --- | --- |
| kind and `scripts/create-cluster.sh` | Cluster, node, host ports and storage parent mount |
| `terraform/argocd` | Argo CD namespace and pinned installation, including CRDs |
| `terraform/platform` | `Application/traefik` in namespace `argocd` |
| Argo CD | Traefik chart resources and app registrations/workloads from `deploy-local` |
| `deploy-local` | App manifests, releases, databases, PV/PVCs, credentials, backups and recovery |

The two Terraform roots are sequential because the Kubernetes provider queries
the API during planning. It cannot plan a custom Application before Argo CD's
CRD is installed. The wrapper waits for Argo CD between the two applies. A
Terraform apply finishing does not itself prove that Traefik or an app is ready.

There is no `local-exec` cluster creation, AWS provider, AWS backend or platform
Git-watching root Application. Terraform reads this checkout. Argo CD reads
Traefik's chart repository; the separate deployment root reads app manifests
from Git. The platform does not need its own Git credential.

## Inspect and apply manually

Run from `infra-local`. Install the README's prerequisites and create the kind
cluster first. For an existing installation, import it before applying.

```sh
umask 077
python3 scripts/render-platform.py
terraform -chdir=terraform/argocd init
terraform -chdir=terraform/argocd plan -out=argocd.tfplan
terraform -chdir=terraform/argocd show argocd.tfplan
```

Rendering downloads the pinned upstream YAML and converts it to a stable JSON
map under ignored `.generated/`. It uses `kubectl patch --local`, which does not
contact the Kubernetes API. Keep `argocd/install/kustomization.yaml` as the
version source; regenerate after every change to that installation. Do not
edit the generated JSON. Each map key identifies API version, kind, namespace
and name, so upstream resource reordering does not change Terraform addresses.

Review the plan before applying. On a fresh cluster it should create the
namespace and installation; on an imported cluster investigate unexpected
changes. When ready to install or update:

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

The wrapper `./scripts/bootstrap-kind.sh` performs these stages with interactive
Terraform plan approval rather than saved plans. It does not use auto-approve.
App credentials and registration still follow `deploy-local` setup afterwards.

## Adopt an existing installation

Do not delete/recreate the cluster, uninstall Argo CD or reset passwords.
First complete any previous repository ownership transfer documented in the
README and ensure the former deployment root no longer owns Traefik.

Back up important app data and preserve Argo CD/Git credentials outside Git.
Confirm the installed Argo CD version and compare local customizations with
`argocd/install/`; an imported resource is still updated to the checked-in
desired configuration when you later apply. Preserve any intended installation
customizations in that Kustomization before rendering and importing.

```sh
python3 scripts/import-platform.py
```

This checks the explicitly selected kind context, renders the installation,
initializes each Terraform root, and imports existing installation objects and
the Traefik Application into their separate local states. It never applies,
patches or deletes Kubernetes objects. Missing objects are left for a later
plan to create. Already tracked addresses are skipped, so an interrupted import
can be rerun. Argo CD's CRDs must already be installed to import its Application.

Review both plans as described above. `force_conflicts` transfers ownership of
declared manifest fields from the retired kubectl installer during a reviewed
apply. Terraform uses typed Secret resources so credentials are marked sensitive
in its plans; their data is ignored so Argo CD/controller-populated keys and
notification credentials are not reset. Git repository Secrets, app Secrets and
the initial-admin-password Secret are not part of this Terraform configuration.
Keep full state files private even though terminal plans redact Secret data.

Once adopted, use Terraform for installation and registration changes. The
remaining shell scripts create kind or verify readiness; none still applies
these resources with kubectl. Traefik's workloads remain Argo CD-owned and must
not be imported into Terraform.

## Local configuration and state

Both roots default to context `kind-argocd-postgres` in `~/.kube/config`.
They do not use the current kubectl context or an ambient `KUBECONFIG` list.
For manual Terraform commands, optionally copy each root's
`terraform.tfvars.example` to its ignored `terraform.tfvars`. Set matching
`cluster_name` and `kubeconfig_path` in both. Shell helpers take a cluster
argument and use `INFRA_LOCAL_KUBECONFIG` for a nondefault kubeconfig:

```sh
python3 scripts/import-platform.py another-cluster --kubeconfig /absolute/kubeconfig
INFRA_LOCAL_KUBECONFIG=/absolute/kubeconfig ./scripts/bootstrap-kind.sh another-cluster
```

Each root uses Terraform's local backend and its own `terraform.tfstate` beside
the `.tf` files. Each also has its own `.terraform/` directory and committed
`.terraform.lock.hcl`. Do not copy AWS state or backend settings here. Use one
cluster per checkout; a different cluster needs its own checkout and state,
not a changed variable against existing state. The helpers reject a state whose
recorded context differs, but manual Terraform commands require the same care.

State and state backups may contain credentials. Keep them out of Git, restrict
permissions, and preserve private copies outside the checkout. The helpers use
`umask 077`; use the same for manual init/import/plan/apply commands. Do not
share binary plans. Never edit state JSON manually.

If you lose state while the cluster still exists, use import to recover ownership
before applying. A fresh clone does not inherit the former checkout's state.
If a deliberately rebuilt cluster has the same name, reuse its private state:
after updating kubeconfig and rendering, Terraform refreshes against the new
cluster and proposes creating missing objects. Apply Argo CD before planning
the platform root again. See [rebuild and recovery](rebuild.md).

All managed resources have `prevent_destroy` to block routine replacement and
destroy plans. This guard does not protect objects when their resource blocks
are removed, or when someone deletes them with kubectl/kind. Never remove the
guards as part of normal bootstrap; deletion needs a separate reviewed procedure
and explicit destructive authorization.

## Validation limits

`./scripts/validate.sh` renders configuration, checks Terraform formatting,
initializes providers without a backend, validates both roots, runs helper tests
and checks Bash syntax and manifest schemas. It needs network access but no
cluster. It does not validate kind configuration or exercise Terraform's live
Kubernetes planning, import, field ownership, or controller readiness. Validate
those against the selected cluster through reviewed plans and readiness checks.

References: [Kubernetes provider planning and import](https://registry.terraform.io/providers/hashicorp/kubernetes/3.4.0/docs/resources/manifest),
[Terraform local backend](https://developer.hashicorp.com/terraform/language/backend/local).
