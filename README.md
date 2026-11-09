# Infrastructure

One repository owns the local kind platform and the existing AWS development
platform. Each target has independent Terraform roots, state, credentials and
plans. Application source remains in `../apps/myfastapiapp`; Argo CD reads
application deployments from `../gitops`, independently of this repository.

| Target | Setup | Terraform roots |
| --- | --- | --- |
| `local` | [kind, controllers and Compose PostgreSQL](environments/local/README.md) | `environments/local/platform` (local state) |
| `aws/dev` | [EKS, RDS, controllers and CloudFront](environments/aws/dev/README.md) | `environments/aws/dev/{foundation,platform,edge}` (existing S3 keys) |
| AWS backend | [separate bootstrap/recovery](environments/aws/dev/docs/teardown.md#rebuild-after-deleting-the-backend) | `bootstrap/aws` |

Run these commands from `infra`:

```sh
./scripts/validate.sh --environment local
./scripts/validate.sh --environment aws/dev
./scripts/validate.sh --all

./scripts/plan.sh --environment local --stage platform --context kind-deployment
./scripts/plan.sh --environment aws/dev --stage foundation
./scripts/plan.sh --environment aws/dev --stage platform --context aws-deployment-project
./scripts/plan.sh --environment aws/dev --stage edge --context aws-deployment-project
```

Validation uses temporary copies and backend-disabled initialization; it needs
internet and each target's documented tools, but no cluster or AWS credentials.
CI checks local and AWS separately on every change, including shared changes.
Configuration validation does not prove deployment health.

Planning verifies the selected target, initializes only its owning backend (and
checks foundation ownership for AWS), then saves a private full plan. Review and
apply that plan separately using the printed command. Kubernetes operations
require an explicit context and support `--kubeconfig /absolute/file`. AWS stage
order remains foundation → platform → app sync/healthy ALB → edge. Backend
bootstrap is separately operated; it is excluded from the planning dispatcher.

[Shared Argo CD values](shared/argocd/values.yaml) load before environment values.
Chart versions stay selectable per environment. A shared edit enters each
consumer's next plan; applying local never applies AWS. Add immutable module
refs or versioned bundles when staging and production need independent desired
versions. Add reusable modules when another environment needs them; this
repository creates no placeholder staging, production or Azure targets.

Read the [environment contract](docs/environments.md) before operational changes
and the [consolidation handoff](docs/consolidation.md) before using existing state
or resuming old teardown records. The original infrastructure checkouts and
workspace `.repo-backups/` are no longer present; legacy recovery needs separately
preserved private artifacts. The repository remote is
[`pquarg/infra`](https://github.com/pquarg/infra). Retiring the old remotes is a
separate step; Argo CD still watches the unchanged GitOps remote.
