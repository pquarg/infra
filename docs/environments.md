# Environment contract

| Concern | Local | AWS development |
| --- | --- | --- |
| Target | `local` | `aws/dev` |
| Cluster/context | kind `deployment` / `kind-deployment` | EKS `deployment-project-dev` / verified explicit context, normally `aws-deployment-project` |
| Controllers | Terraform Helm: Argo CD and Traefik | Terraform Helm: Argo CD and AWS Load Balancer Controller |
| App ownership | Argo CD `bootstrap`, `apps`, `<app>` | Argo CD `bootstrap`, `apps`, `<app>` |
| App sync | Automatic | Manual |
| App namespace | `<app>` | `<app>` |
| Database | Compose, `host.docker.internal:5433` from kind Pods | Private RDS with verified TLS |
| State | `environments/local/platform/terraform.tfstate` | Protected S3 bucket and separate stage keys |
| Credentials | Private kubeconfig and database passwords | Account `245361883993`, profile `aws-project`, region `eu-north-1` |

kind owns the local cluster; Compose owns PostgreSQL; Terraform owns controllers
and AWS infrastructure; Argo CD owns app resources; the AWS controller owns ALBs.
Each target owns its resource addresses, lifecycle protections and version pins.
Commit provider locks with the target configuration. Local state has no AWS
backend or provider. AWS bootstrap uses `bootstrap/terraform.tfstate`; foundation
uses `dev/terraform.tfstate`, platform uses `dev/platform/terraform.tfstate`, and
edge uses `dev/edge/terraform.tfstate` in the protected state bucket.

Local host ports are selected in the ignored kind configuration. Compose uses
project `deployment-project-local-db` and external volume
`deployment-project-myfastapiapp-postgres`. Password files and database backups
live outside the checkout. Keep their identities stable during maintenance.

GitOps selects `--environment local|aws`; its `aws` maps to this
repository's `aws/dev`.

The registration chain is `bootstrap` → `apps` → `<app>` in both clusters.
Each Application selects only its environment's overlay from GitOps Git.

Every new target needs independent roots, state, credentials, identity checks
and explicit selection. Keep provider and backend selection at the target root.
Shared files use the checked-out commit; production that needs an older desired
implementation must select immutable module refs or versioned bundles explicitly.
Never run a combined apply or destroy across targets.

## Application identity

The application and namespace are `demo-api`; the Python package is `demo_api`.
The database and restricted database login are named `myfastapiapp` in both
environments. The AWS ALB selected by GitOps and edge is `dev-demo-api`.
Application identity, database identity and Terraform resource addresses are
separate contracts. Review all consumers before changing any of them.
