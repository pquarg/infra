# How the AWS platform works

Terraform creates the foundation, installs shared software with Helm and adds
CloudFront after the app creates its private ALB. Argo CD reads application
manifests from `gitops`. These repositories remain independent.

```text
Client → HTTPS CloudFront → VPC origin → private ALB → EKS FastAPI → private RDS
                                            ↑
                       AWS Load Balancer Controller reads the app Ingress
```

| Owner | Configuration and effect |
| --- | --- |
| Backend bootstrap | [`../../../bootstrap/aws/`](../../../../bootstrap/aws) owns only the protected S3 state bucket |
| Foundation | [`foundation/`](../foundation) owns VPC/subnets/NAT, EKS/workers/add-ons, RDS, controller IAM/policy/Pod Identity and frontend SG/rules |
| Platform | [`platform/`](../platform) owns real Helm releases `argocd` and `aws-load-balancer-controller` |
| App GitOps | `gitops` owns ApplicationSet, Deployment, Service, ConfigMaps, migration Job and Ingress; app sync remains manual |
| Load balancer controller | Owns the app ALB, target groups and backend networking; Terraform only looks up the ALB |
| Edge | [`edge/`](../edge) owns CloudFront origin/distribution/functions/headers/log delivery and the log bucket |
| Credential provisioning | App database login, database Secret, GHCR Secret and Argo CD Git credential stay outside Terraform/app Git |

Foundation has public NAT subnets, private worker/ALB subnets and isolated database
subnets in two Stockholm AZs. Workers remain two `t3.small` nodes; EKS uses Pod
Identity, not IRSA. Public API access is limited by `admin_ipv4_cidrs`. RDS remains
single-AZ PostgreSQL 17 on `db.t4g.micro`, with encrypted storage, worker-only
5432 access, TLS required, managed administrator password, one-day backups,
deletion protection, required final snapshot and retained automated backups.

CloudFront retains HTTPS-only viewers, disabled API caching, host forwarding,
response security headers, redirect handling and the same operator IPv4 allowlist.
The private origin leg uses HTTP. Its frontend group admits HTTP only from the
CloudFront origin-facing prefix list and sends app/check traffic to port 8000.
S3 access logs remain private/encrypted with 14-day expiry and omit query strings,
cookies, authorization, referer and viewer IPs. Global-service log delivery stays
explicitly in `us-east-1`; regional resources stay `eu-north-1`. EKS logs retain
seven-day expiry. This lab keeps one app replica, a single-AZ DB and shared NAT;
review availability, security and version changes through the owning stage plan.

## Stage interfaces and state

All state uses the same existing encrypted/locked/versioned bucket, with keys:

- `bootstrap/terraform.tfstate`: backend bootstrap, unchanged.
- `dev/terraform.tfstate`: foundation.
- `dev/platform/terraform.tfstate`: the two Helm releases.
- `dev/edge/terraform.tfstate`: CloudFront and logs.

Stage discovery uses AWS data sources and small non-secret inputs rather than `terraform_remote_state`. Platform
reads EKS endpoint/CA/VPC metadata from the selected cluster and uses AWS token
exec auth. Edge discovers that cluster/VPC and the named frontend SG, then checks
the ALB is internal/IPv4/in the expected VPC with that group attached.

[`config/operator.tfvars.example`](../config/operator.tfvars.example) documents
the single ignored allowlist used for foundation and edge. Passwords and tokens
are never outputs. Foundation's `database` output contains connection metadata
and a Secrets Manager ARN, not its password. Platform chart pins and values live
in [`argocd/`](../argocd) and
[`load-balancer-controller/`](../load-balancer-controller). The controller policy
remains pinned in [`policies/`](../policies); functions/tests remain alongside the stage roots in `environments/aws/dev`.

## Operating sequence

1. Ensure the backend exists, then review/apply a full foundation plan.
2. Verify endpoint, nodes and Pod Identity prerequisites; review/apply platform.
3. Follow `gitops` credential/setup steps, register `bootstrap`, manually sync
   `demo-api` including its migration hook.
4. Wait for an Active controller-created ALB with healthy targets, then review/
   apply edge. Verify HTTPS, app/database authentication, address restrictions and
   actual logs. Finish with full plans for all deployed roots.

The [public planner](../../../../scripts/plan.sh), with `--environment aws/dev`
and an explicit `--stage`, selects one root and clears ambient Terraform
workspace/backend arguments. It delegates to the stage helper, which invokes
[`scripts/check-stage.sh`](../scripts/check-stage.sh) before saving a full plan.
Platform and edge also require an explicit `--context`. Foundation is valid
before an ALB exists; no bootstrap targets are required. Separate roots cannot
express a Terraform dependency graph for removal: destroy edge, pause app
reconciliation/remove Ingresses, wait for the running controller to remove ALBs/
targets, remove platform, remove foundation, then optionally remove backend last.
See [teardown](teardown.md). Validation runs offline tests and backend-disabled
Terraform checks; it does not establish runtime health.
