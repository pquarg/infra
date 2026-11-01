# Deployment project infrastructure

Terraform for project `245361883993`, AWS profile `aws-project`, selected Region
`eu-north-1` (Stockholm). Regional resources stay in Stockholm; CloudFront is
global and its standard-log delivery configuration uses `us-east-1` as required.

For a high-level explanation of the components, request flow, ownership, and
deployment lifecycle, read [How this repository works](docs/architecture.md).

## Current deployment

Verified **2026-11-01**: EKS workers Ready, Argo CD/app Synced and Healthy,
PostgreSQL migrations complete, internal ALB Active with a Healthy target,
CloudFront VPC origin and distribution Deployed. Public health/readiness,
Swagger/OpenAPI, HTTPS redirects, registration/login/logout, token revocation
and no-store behavior passed. Access logs arrived in the private encrypted S3
bucket with the selected fields. The full post-apply Terraform plan reports
**No changes**.

- [Public API docs](https://d1dgp19b9ypb8r.cloudfront.net/api/v1/docs/)
- Distribution: `E318GC97RVXQ86`
- EKS: `deployment-project-dev`; explicit context: `aws-deployment-project`
- Database: `deployment-project-dev-postgres`, private RDS PostgreSQL
- Internal ALB: `deployment-project-dev-app`, owned by the Kubernetes controller
- Logs: `deployment-project-cf-logs-245361883993-eu-north-1`, 14-day expiry
- [Complete deployment/rebuild walkthrough](../deploy-aws/docs/deploy-from-scratch.md)
- [Single-command complete teardown](docs/teardown.md)
- [Manual teardown with retention choices](../deploy-aws/docs/teardown.md)
- [Deployment handoff](../deploy-aws/docs/deployment-progress.md)
- [Public access runbook](../deploy-aws/docs/public-access.md)

This is a learning environment: two small workers and one app replica. App syncs
remain manual. WAF is not configured. Resources continue to incur usage while
running; use the [cleanup plan](CLEANUP_PLAN.md) for deliberate removal.

## Tear down everything in this deployment

From this repository:

```sh
./scripts/teardown-all.py
```

Type the project ID once to confirm. This removes the runtime, **all database
snapshots/retained backups, access logs and the Terraform state bucket/history**.
Use `--check` for read-only AWS preflight or `--yes` to skip the initial prompt.
The command handles CloudFront → Kubernetes/ALB → runtime → state backend order
using private configuration copies, without editing source files or publishing
Git changes. It records progress for `--resume` after interruption. See the
[command runbook](docs/teardown.md) for prerequisites, scope and rebuilding the
backend afterward. Offline tests pass; live deletion has not been exercised.

## Operator-IP restriction — prepared, not applied

The user requested access only from their own public IP. `cloudfront.tf` now
adds a viewer-request function that allows only the exact IPv4 `/32` addresses
in the ignored `eks.auto.tfvars` variable `eks_admin_cidrs`. The same list now
restricts both Kubernetes API access and all app endpoints. The function uses
CloudFront's connection IP rather than forwarded headers; every other address
receives 403 before reaching the origin. IPv6 is disabled so browsers use the
IPv4 allowlist. The existing private ALB and security groups stay in place.

Terraform validation and all 12 function tests passed. The full saved plan
`app-ip-allowlist.tfplan` proposes **1 addition, 1 in-place change, 0 deletions**.
It creates the function and updates CloudFront. The live app remains public
until the user applies this plan and the distribution finishes deploying:

```sh
terraform show -no-color app-ip-allowlist.tfplan
terraform apply app-ip-allowlist.tfplan
```

Verify the app works from the allowed connection and returns 403 from another
network (for example, a phone with Wi-Fi off). Run a full plan afterward and
expect no changes. See the [access and recovery runbook](../deploy-aws/docs/public-access.md#restrict-the-app-to-the-operators-ip--prepared-not-applied).

Anyone sharing the allowed public IP is allowed through this network check;
application authentication still applies. A changed ISP/VPN IPv4 requires an
updated ignored allowlist and a new reviewed plan/apply, affecting both EKS and
the app. IPv6-only clients cannot connect. CloudFront Function invocations have
the existing usage-based billing; no WAF subscription is added.

## Ownership and state

This repository manages networking, EKS/worker IAM, RDS, the Argo CD and load
balancer controller Helm releases, the ALB frontend group, CloudFront and logs.
`deploy-aws` manages app resources through Argo CD. The controller owns the ALB,
target groups and backend rules; `cloudfront-origin.tf` only looks up the ALB.
Database and registry credentials are provisioned outside Terraform/app Git.

Both Terraform directories use the retained S3 bucket
`deployment-project-tfstate-245361883993-eu-north-1`, encrypted with native S3
locking and explicit profile/Region/project restrictions:

| Directory | State key |
| --- | --- |
| `bootstrap/` | `bootstrap/terraform.tfstate` |
| repository root | `dev/terraform.tfstate` |

Commit Terraform/module configuration, both provider lock files, public policy
JSON, function code/tests and the operator-IP example. Keep `.terraform/`, real
`.tfvars`, state, binary plans, passwords and private keys outside Git. The real
`eks.auto.tfvars` contains the operator's current IPv4 `/32`, shared by EKS and
app access; it is ignored.
All saved creation plans from this session are consumed/stale.

## Validation and changes

From this repository, with the existing `aws-project` login:

```sh
terraform init
terraform validate
node --test tests/*.test.cjs
python3 -m unittest discover -s tests -p 'test_teardown_all.py'
terraform plan
```

Review and save a fresh plan for any intended infrastructure change; the user
runs applies. Publishing these files does not deploy app code. An app image or
manifest change is released through `deploy-aws` and a full manual app sync.

## Rebuilding after complete removal

Keep the state bootstrap separate. Initialize the retained backend, update the
ignored operator-IP file, and check the target project before planning. A full
root plan cannot look up an app ALB that does not exist yet. Rebuild in stages:

1. Plan/apply the VPC, EKS and fresh RDS dependencies first, using reviewed
   targets `module.vpc`, `module.eks`, and `aws_db_instance.postgres`.
2. Once EKS is reachable, plan/apply the two Helm releases and both app frontend
   rule resources as a second targeted bootstrap stage. Their dependencies
   include the frontend group and controller IAM/Pod Identity configuration.
3. Refresh the explicit kubeconfig context. Recreate database, registry and Git
   credentials through the deployment runbooks. Update the app Ingress with the
   new frontend group ID, publish it, bootstrap registrations and fully sync it.
4. Wait for the internal ALB to become Active with Healthy targets, then review
   a full **untargeted** root plan for the VPC origin, distribution and logging.
5. Verify the public endpoint and a clean full plan. Targeting is a bootstrap
   exception; subsequent routine plans must be untargeted.

RDS deletion protection, `prevent_destroy`, a required final snapshot and
retained automated backups are enabled. The October 31 instance was deleted
without backups; those records cannot be recovered. The current rebuilt
database has one-day backups. See the cleanup plan before changing safeguards.
