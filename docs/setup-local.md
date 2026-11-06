# Build and deploy locally with one entry point

Run from `infra-local`, with the sibling `gitops` and `apps/myfastapiapp` checkouts
present. Start Docker Desktop and install the [prerequisites](../README.md#prerequisites),
including Python 3.11+, before running:

```sh
./scripts/setup-local.sh --check
./scripts/setup-local.sh
```

`--check` checks tools, Docker access, GitOps publication, app source/image version,
existing-cluster identity/legacy workloads and both repository validators. It
does not create database settings, credentials, volumes or Kubernetes resources.
Validation fetches dependencies/schemas and needs internet access. It does not
prove deployment readiness.

The normal run shows eight stages:

1. Validate prerequisites and both repositories. Deployment manifests must match
   published `gitops/main`; the app checkout must be clean and its version must
   match the selected local image tag. The script does not commit, push or change
   the selected release.
2. Create missing local database configuration and random admin/app passwords,
   then start Compose PostgreSQL. Existing `.env`, passwords and volumes are
   reused. Existing volumes with missing credentials/settings stop setup for
   recovery rather than silently generating replacement passwords.
3. Create kind only when absent, preserving existing ignored kind configuration.
   Bootstrap Argo CD/Traefik with the existing Terraform wrapper. Review its plan
   and enter `yes` at Terraform's prompt. Existing ownership/adoption checks stay
   in effect; there is no automatic approval or cluster rebuild.
4. Create the app namespace and connection Secret from the Compose app password.
   Existing matching connection Secrets are reused. A differing Secret stops
   setup for deliberate migration/rotation. Configure or reuse Argo CD's Git
   credential without printing the private key or writing filled Secret files.
5. Build the selected app image and load it into kind. This path does not need a
   GHCR token; it creates an empty registry auth config only if the referenced
   pull Secret is missing. Existing registry credentials are kept. For future
   private registry pulls, use GitOps's `configure-registry.py` helper separately.
6. Run the existing Pod database connectivity/privilege probe with the selected
   image and explicit local environment/context.
7. Register only local GitOps, then wait up to ten minutes for the app to be
   Synced/Healthy at the published revision with a successful sync operation.
   Existing maintenance pauses are respected; setup does not force a sync.
8. Check migration logs, API rollout, Alembic heads and `/health`/`/ready` through
   Traefik. Test registration/login yourself at the printed app URL afterward.

The script targets only `kind-argocd-postgres`. It explicitly selects one
kubeconfig for kind/platform/GitOps operations, even if your current context is
AWS. To use another kubeconfig file for this same local cluster:

```sh
./scripts/setup-local.sh --kubeconfig /absolute/path/to/kubeconfig
```

It does not install system tools, reset databases, delete clusters/volumes, rotate
credentials, transfer legacy database records or apply AWS infrastructure.

## One-time GitHub deploy key

Argo CD needs its own read access to `git@github.com:pquarg/gitops.git`. Your laptop's
SSH agent is not automatically available inside its Pods. Existing matching Argo CD
repository credentials are reused. Otherwise, provide an existing unencrypted,
read-only deploy-key private file:

```sh
./scripts/setup-local.sh --git-ssh-key "$HOME/.ssh/deployment-project-gitops"
```

If you need a new key, generate a dedicated one outside Git. Run this only if the
file does not already exist; preserve existing keys:

```sh
ssh-keygen -t ed25519 -N '' \
  -C local-argocd-gitops \
  -f "$HOME/.ssh/deployment-project-gitops"
```

Authorize the **public** `.pub` key in the GitHub repository's **Settings → Deploy
keys → Add deploy key**, leaving write access disabled. The private file stays on
your laptop and is supplied to Argo CD through a Kubernetes Secret. No Argo CD UI
login is required for this setup path. The script checks that the private key is
readable without a passphrase; application sync subsequently verifies actual Git
access. Host verification remains enabled. See [Argo CD private repository setup](https://argo-cd.readthedocs.io/en/stable/user-guide/private-repositories/).

## Rerun and diagnose

Rerun the same command after fixing an error. Successfully created resources are
retained on failure or interruption. The script prints which stage failed; it
never tears down the platform to recover. It rebuilds/loads the selected image
and reruns the reviewed platform bootstrap on subsequent runs.

An active legacy Kubernetes PostgreSQL workload stops setup before provisioning.
Use the [database cutover](../../gitops/docs/local/database-migration.md) manually.
Retained stopped legacy resources can keep an app OutOfSync: this script requires
Synced and will stop rather than pruning them. Follow the cutover runbook to
verify that installation and retire legacy resources only with authorization.

For a database configuration/password failure, follow [database recovery](database.md).
For existing untracked controllers/state, follow [Terraform adoption](terraform.md#adopt-an-existing-installation).
For app sync failures, inspect explicitly:

```sh
kubectl --context kind-argocd-postgres -n argocd get applications
kubectl --context kind-argocd-postgres -n myfastapiapp get pods,jobs
kubectl --context kind-argocd-postgres -n myfastapiapp logs job/myfastapiapp-migrate
```

After a successful run, the app is at http://myfastapiapp.localhost/api/v1/docs/.
The script also prints an optional Argo CD port-forward command; use the
[sign-in instructions](../README.md#2-install-argo-cd-and-shared-ingress) to open its UI.
