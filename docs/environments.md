# Environment contract

| Concern | Local | AWS development |
| --- | --- | --- |
| Target | `local` | `aws/dev` (existing identities keep `dev`) |
| Cluster/context | kind `deployment` / `kind-deployment` | EKS `deployment-project-dev` / verified explicit context, normally `aws-deployment-project` |
| Controllers | Terraform Helm: Argo CD and Traefik | Terraform Helm: Argo CD and AWS Load Balancer Controller |
| App ownership | Argo CD `bootstrap`, `apps`, `<app>` | Argo CD `bootstrap`, `apps`, `<app>` |
| App sync | Automatic | Manual |
| App namespace | `<app>` | `<app>` |
| Database | Compose, `host.docker.internal:5433` from kind Pods | Private RDS with verified TLS |
| State | `environments/local/platform/terraform.tfstate` | Existing S3 bucket and `dev/` keys |
| Credentials | Existing private kubeconfig and database passwords | Account `245361883993`, profile `aws-project`, region `eu-north-1` |

kind owns the local cluster; Compose owns PostgreSQL; Terraform owns controllers
and AWS infrastructure; Argo CD owns app resources; the AWS controller owns ALBs.
Resource addresses, lifecycle protections, module/chart/provider pins and provider
locks are retained. Local state has no AWS backend or provider. AWS bootstrap
keeps `bootstrap/terraform.tfstate`; foundation keeps `dev/terraform.tfstate`,
platform keeps `dev/platform/terraform.tfstate`, and edge keeps
`dev/edge/terraform.tfstate` in the existing protected bucket.

Local host ports and ignored kind configuration remain unchanged. Compose keeps
project `deployment-project-local-db` and external volume
`deployment-project-myfastapiapp-postgres`. No cluster, volume, database, password
or existing external secrets directory is renamed or recreated by consolidation.

GitOps continues selecting `--environment local|aws`; its `aws` maps to this
repository's `aws/dev`. Consolidation changes infrastructure paths, leaving app
bases, overlays, image selection, registration identities and Git remote intact.

The later GitOps naming change uses `bootstrap` → `apps` → `<app>` in both
clusters. Existing registrations need the
[registration migration](../../gitops/docs/registration-migration.md) before
publishing that change; infrastructure names and Terraform state stay unchanged.

Every new target needs independent roots, state, credentials, identity checks
and explicit selection. Keep provider and backend selection at the target root.
Shared files use the checked-out commit; production that needs an older desired
implementation must select immutable module refs or versioned bundles explicitly.
Never run a combined apply or destroy across targets.

## App naming handoff

The desired app/namespace is now `demo-api`; the Python package is `demo_api`.
The existing database/login, Compose projects/volumes, credential paths, cluster
names and Terraform resource addresses remain stable. The AWS ALB selected by
GitOps and edge is now `dev-demo-api`. Follow the
[app rename handoff](../../gitops/docs/app-rename.md) before publishing GitOps
or applying an edge plan to an existing environment.
