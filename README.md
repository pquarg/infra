# Deployment project infrastructure

Terraform configuration for a small FastAPI deployment in AWS project
`245361883993`, using profile `aws-project` and selected Region `eu-north-1`
(Stockholm). Regional resources are configured in Stockholm; CloudFront is
global and its standard-log delivery configuration uses `us-east-1` as required.

These docs describe the configuration and deployment procedures. They apply
whether the infrastructure is running, partially deployed, or removed; they do
not assert that any AWS resource currently exists.

## Start here

Read [How this repository works](docs/architecture.md) for the request flow,
component ownership, repository map, and deployment lifecycle.

The configured request path is:

```text
Browser/API client → CloudFront → private ALB → FastAPI on EKS → PostgreSQL
```

Terraform defines the network, EKS workers, RDS database, Argo CD and load
balancer controller installations, CloudFront, and logs. The companion
[`deploy-aws` repository](../deploy-aws/README.md) defines the Kubernetes
application resources. The load balancer controller creates the ALB from the
application Ingress; Terraform looks it up rather than managing its lifecycle.

This is a learning configuration: two small workers, one app replica, a
single-AZ database, manual app syncs, and no WAF. Provisioned resources incur
usage charges until removed; retained backups, logs, and state can also incur
charges after the runtime is removed.

## Runbooks

- [Complete deployment/rebuild walkthrough](../deploy-aws/docs/deploy-from-scratch.md)
- [Single-command complete teardown](docs/teardown.md)
- [Cleanup order and retention choices](CLEANUP_PLAN.md)
- [Manual teardown walkthrough](../deploy-aws/docs/teardown.md)
- [Public access and recovery runbook](../deploy-aws/docs/public-access.md)

## Operator-IP restriction

`cloudfront.tf` configures a viewer-request function that allows only the exact
IPv4 `/32` addresses supplied through `eks_admin_cidrs` in the ignored
`eks.auto.tfvars` file. The same list restricts public Kubernetes API access
and all app endpoints. The function uses CloudFront's connection IP rather
than forwarded headers; other addresses receive `403` before reaching the
origin. CloudFront IPv6 is disabled to use the IPv4 allowlist.

Use [`eks.auto.tfvars.example`](eks.auto.tfvars.example) as the template for
your private values. Deploy changes through a fresh, reviewed Terraform plan.
After deployment, verify access from an allowed connection and rejection from
another network, such as a phone with Wi-Fi off. See the
[public access runbook](../deploy-aws/docs/public-access.md) for recovery steps.

Anyone sharing an allowed public IP passes this network check; application
authentication still applies. A changed ISP/VPN IPv4 requires updating the
ignored allowlist and reviewing/applying a new plan, affecting both EKS and the
app. IPv6-only clients cannot connect.

## Terraform state

Both Terraform directories are configured to use the S3 bucket
`deployment-project-tfstate-245361883993-eu-north-1`, with encryption, native S3
locking, and explicit profile/Region/project restrictions:

| Directory | State key |
| --- | --- |
| `bootstrap/` | `bootstrap/terraform.tfstate` |
| repository root | `dev/terraform.tfstate` |

`bootstrap/` defines the protected, versioned state bucket separately from the
runtime. Both checked-in backends require the bucket to exist before normal
initialization. If it is absent, follow the
[backend creation procedure](docs/teardown.md#rebuild-after-deleting-the-backend)
first. Removing the runtime does not necessarily remove the backend; this
depends on the teardown procedure and retention choices.

Commit Terraform/module configuration, both provider lock files, public policy
JSON, function code/tests, and the operator-IP example. Keep `.terraform/`, real
`.tfvars`, state, binary plans, passwords, and private keys outside Git.
Database application credentials and registry/Git credentials are provisioned
through the deployment runbooks outside Terraform/app Git.

## Deploying or rebuilding

Ensure the state bucket exists, configure the ignored operator-IP file, and
check the target project before planning. Follow the
[deployment walkthrough](../deploy-aws/docs/deploy-from-scratch.md) in stages:

1. Plan/apply the VPC, EKS, and RDS dependencies, using reviewed targets
   `module.vpc`, `module.eks`, and `aws_db_instance.postgres`.
2. Once EKS is reachable, plan/apply the two Helm releases and both app frontend
   rule resources as a second targeted bootstrap stage. Their dependencies
   include the frontend security group and controller IAM/Pod Identity setup.
3. Configure the kubeconfig context and provision database, registry, and Git
   credentials through the deployment runbooks. Set the app Ingress's frontend
   security-group ID from Terraform outputs, publish it, register the app, and
   perform a full manual sync including migrations.
4. Wait for the controller-created internal ALB to become Active with Healthy
   targets, then review a full **untargeted** root plan for the VPC origin,
   distribution, and logging.
5. After applying, verify health/readiness, API docs, authentication, HTTPS
   redirects, IP restrictions, and log delivery. Run a full plan to check that
   the deployed infrastructure matches the configuration.

A full root plan cannot look up an app ALB before the application Ingress has
created it. Targeting is a bootstrap exception; routine plans are untargeted.
For a partial deployment, establish which stages are complete before continuing.

Once deployed, use `terraform output myfastapiapp_https` from the initialized
root directory to obtain the generated CloudFront endpoint and API docs URL.
These values can change between deployments.

## Validation and changes

The function and teardown tests run locally without a deployed environment:

```sh
node --test tests/*.test.cjs
python3 -m unittest discover -s tests -p 'test_teardown_all.py'
```

With Terraform installed, validate configuration without initializing the S3
backend. Initialization may download modules/providers but does not deploy
resources:

```sh
terraform init -backend=false
terraform validate
terraform -chdir=bootstrap init -backend=false
terraform -chdir=bootstrap validate
```

For routine changes, with the backend and deployment dependencies available
and an active `aws-project` login, initialize the backend and generate a fresh
plan:

```sh
terraform init
terraform plan -out=infra.tfplan
terraform show -no-color infra.tfplan
```

Review each plan before applying it; the operator runs applies. Generate a new
plan after deployment, teardown, or configuration changes rather than reusing
an earlier saved plan. Publishing these files does not deploy app code: image
or manifest changes are released through `deploy-aws` and a full manual app sync.

## Removal and retention

The configuration enables RDS deletion protection, `prevent_destroy`, a required
final snapshot, retained automated backups, and one-day backup retention. The
log bucket has 14-day expiry and refuses deletion while nonempty by default.
Choose retention deliberately before changing these safeguards.

For complete removal of the deployment, including **all database snapshots and
retained backups, access logs, and the Terraform state bucket/history**, follow
the [complete teardown runbook](docs/teardown.md). Its command uses private
configuration copies and records progress for resuming an interrupted teardown.

For a teardown with database/log retention choices and a retained state backend,
use the [cleanup plan](CLEANUP_PLAN.md) and
[manual walkthrough](../deploy-aws/docs/teardown.md).
