# Deployment project infrastructure

Terraform for project `245361883993`, AWS profile `aws-project`, selected Region
`eu-north-1` (Stockholm). Regional resources stay in Stockholm; CloudFront is
global and its standard-log delivery configuration uses `us-east-1` as required.

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
- [Complete teardown walkthrough](../deploy-aws/docs/teardown.md)
- [Deployment handoff](../deploy-aws/docs/deployment-progress.md)
- [Public access runbook](../deploy-aws/docs/public-access.md)

This is a learning environment: two small workers and one app replica. App syncs
remain manual. WAF is not configured. Resources continue to incur usage while
running; use the [cleanup plan](CLEANUP_PLAN.md) for deliberate removal.

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
`eks.auto.tfvars` contains the operator's current IPv4 `/32`; it is ignored.
All saved creation plans from this session are consumed/stale.

## Validation and changes

From this repository, with the existing `aws-project` login:

```sh
terraform init
terraform validate
node --test tests/https-redirects.test.cjs
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
