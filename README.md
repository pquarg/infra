# Infrastructure

This repository provides Terraform and operational tooling for a local kind
platform and an AWS development platform. Each target has independent Terraform
roots, state, credentials and plans. Application source lives in the
[demo-api repository](https://github.com/pquarg/myfastapiapp/blob/main/README.md); Argo CD reads application
deployments from the independent [GitOps repository](https://github.com/pquarg/gitops/blob/main/README.md).
Use the [workspace layout](https://github.com/pquarg/gitops/blob/main/README.md#get-the-companion-repositories)
when following setup commands across repositories.

| Target | Setup | Terraform roots |
| --- | --- | --- |
| `local` | [kind, controllers and Compose PostgreSQL](environments/local/README.md) | `environments/local/platform` (local state) |
| `aws/dev` | [EKS, RDS, controllers and CloudFront](environments/aws/dev/README.md) | `environments/aws/dev/{foundation,platform,edge}` (separate S3 keys) |
| AWS backend | [separate bootstrap/recovery](docs/state.md#create-the-aws-state-backend) | `bootstrap/aws` |

Start with the selected environment's setup guide. Local setup provisions the
database, kind and controllers before handing application deployment to GitOps.
AWS setup proceeds through the state backend, foundation, platform, app sync and
edge. The AWS target is configured for a specific account, profile and region;
review its identity checks before provisioning.

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
and [state and recovery](docs/state.md) before backend setup or state recovery.
Private credentials, state and backups are separate from Git source history.
The repository remote is [`pquarg/infra`](https://github.com/pquarg/infra).

See [Gateway API lifecycle](shared/gateway-api.md) for the Standard `v1.6.2` pin,
controller CRD ownership, readiness and deletion ordering. AWS changes are
configuration preparation for its first deployment; no AWS runtime was verified.
