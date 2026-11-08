# AWS infrastructure

This environment in the consolidated `infra` repository owns the AWS foundation, shared Kubernetes platform
and CloudFront edge for `gitops`. Account `245361883993`, profile `aws-project`,
Region `eu-north-1`. CloudFront is global; its log delivery uses `us-east-1`.
These files describe desired configuration, not current live health.

The request path is client → CloudFront → private controller-owned ALB → FastAPI
on EKS → RDS PostgreSQL. The load balancer controller creates the ALB from an app
Ingress; Terraform never owns that ALB or its targets.

| Stage | Configuration | State key |
| --- | --- | --- |
| Backend bootstrap | `../../../bootstrap/aws/` | `bootstrap/terraform.tfstate` |
| Foundation | `foundation/`: VPC, EKS, RDS, controller IAM/Pod Identity, frontend SG/rules | `dev/terraform.tfstate` |
| Platform | `platform/`: Argo CD and load balancer controller Helm releases | `dev/platform/terraform.tfstate` |
| Edge | `edge/`: ALB lookup, CloudFront, functions and access logs | `dev/edge/terraform.tfstate` |

All keys use the existing protected/versioned S3 bucket
`deployment-project-tfstate-245361883993-eu-north-1`, encryption and native locking.
Provider locks and module/chart/image pins are committed. Run Terraform from
the owning stage directory; the repository root contains no Terraform configuration.

Run commands here from `infra/environments/aws/dev` unless stated otherwise.
The public repository interface requires `--environment aws/dev`; the helpers
here are scoped to this existing target.

## Setup and changes

Application deployments use [the GitOps repository](../../../../gitops/README.md).

Install Terraform `1.17.x` (CI `1.17.1`), AWS CLI v2, kubectl, Python 3 and Node.js
for tests. Use the existing AWS profile and authenticate it separately.
Prepare one ignored operator configuration:

```sh
cp -n config/operator.tfvars.example config/operator.tfvars
chmod 600 config/operator.tfvars
```

Replace its example with your approved public IPv4 `/32` addresses. Foundation
and edge read this same file; updating it requires reviewing/applying **both**
stages to keep API and CloudFront restrictions identical. No passwords belong
in it. Keep real variables, state, plans and credentials out of Git.

With the state backend available, plan each stage through the repository's
public planner. These commands still run from `infra/environments/aws/dev`:

```sh
../../../scripts/plan.sh --environment aws/dev --stage foundation
# Review saved plan before applying it:
terraform -chdir=foundation apply stage.tfplan
aws eks update-kubeconfig --name deployment-project-dev --profile aws-project \
  --region eu-north-1 --alias aws-deployment-project
../../../scripts/plan.sh --environment aws/dev --stage platform --context aws-deployment-project
terraform -chdir=platform apply stage.tfplan
```

Foundation needs no Kubernetes provider and no ALB. Platform discovers the
selected EKS endpoint/CA through AWS and gets tokens through `aws eks get-token`.
The prerequisite checker verifies the Kubernetes endpoint and Pod Identity agent/
association. Values/pins live in `argocd/` and `load-balancer-controller/`, matching
local's controller values-file layout.

Both environments select Argo CD chart `10.11.6` (Argo CD `v3.5.6`) and its default
Dex/Redis images. The local kind example pins Kubernetes `1.36.4` by
digest, sharing EKS's configured `1.36` minor version. Controller upgrades use a
reviewed platform plan/apply, independently of app GitOps. See the
[local version upgrade notes](../../local/docs/terraform.md#shared-versions-and-upgrades).

Follow [gitops setup](../../../../gitops/docs/aws/setup.md) to provision credentials,
register apps and manually sync them. Wait for an Active ALB with healthy targets
before edge (the checker refuses a missing or unhealthy origin):

```sh
../../../scripts/plan.sh --environment aws/dev --stage edge --context aws-deployment-project
terraform -chdir=edge apply stage.tfplan
terraform -chdir=edge output myfastapiapp_https
```

The public planner verifies target selection and clears ambient Terraform
workspace/backend arguments before invoking the stage helpers. Use it for normal
planning rather than calling the internal `scripts/plan.sh` directly.
No bootstrap `-target` sequence is needed. Review full plans for each stage.
`--kubeconfig /absolute/file` is supported by stage checks/planning. No stage
reads another stage's sensitive state. Inspect non-secret outputs from the
owning roots: foundation exposes network/cluster/database/controller-role/SG
metadata; edge exposes origin and HTTPS metadata.

## Validation and changes

```sh
./scripts/validate.sh
git diff --check
```

Validation uses temporary configuration copies, backend-disabled init and
committed locks. It runs Terraform format/validate, shell stage safety checks,
CloudFront function tests and teardown regressions. It downloads pinned modules/
providers and needs internet, but no AWS credentials or live cluster. CI runs the
same checks. Passing checks do not prove deployment success.

Provider lock files must include package checksums for both Apple Silicon Macs
(`darwin_arm64`) and the GitHub Actions runner (`linux_amd64`). After a reviewed
provider update, run this in each affected, initialized stage (foundation shown):

```sh
terraform -chdir=foundation providers lock \
  -platform=darwin_arm64 -platform=linux_amd64
```

Commit the updated `.terraform.lock.hcl` files. CI deliberately initializes with
`-lockfile=readonly`, so it cannot add a missing platform's checksum. See
[Terraform's provider lock command](https://developer.hashicorp.com/terraform/cli/commands/providers/lock).

For live acceptance check controller/app health, database data/authentication,
ALB target health, HTTPS, allowed/denied addresses and actual log receipt; finish
with ordinary plans showing no unexpected changes. Changes to cluster/storage,
retention or versions are separate work. No apply, destroy or state transfer is
performed by validation.

## Recovery and removal

Use [teardown/rebuild](docs/teardown.md) and
[retention choices](CLEANUP_PLAN.md). Across roots, Terraform cannot enforce
deletion dependencies: edge must go first, then app Ingresses while the controller
runs, then platform, then foundation. Backend deletion is a separately selected
last scope.

Read [architecture](docs/architecture.md),
[full deployment walkthrough](../../../../gitops/docs/aws/deploy-from-scratch.md) and
[public access verification](../../../../gitops/docs/aws/public-access.md) before live work.
