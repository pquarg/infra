# Terraform state and recovery

Terraform state binds configuration to managed resources. Each target and stage
has one authoritative state location. Git stores configuration and provider
locks; it does not store state, credentials, saved plans or database backups.
Paths on this page are relative to the `infra` repository root.

## State locations

| Root | State location |
| --- | --- |
| `environments/local/platform` | Local `terraform.tfstate` in that root |
| `bootstrap/aws` | S3 key `bootstrap/terraform.tfstate` |
| `environments/aws/dev/foundation` | S3 key `dev/terraform.tfstate` |
| `environments/aws/dev/platform` | S3 key `dev/platform/terraform.tfstate` |
| `environments/aws/dev/edge` | S3 key `dev/edge/terraform.tfstate` |

AWS roots use bucket `deployment-project-tfstate-245361883993-eu-north-1`,
profile `aws-project`, account `245361883993` and Region `eu-north-1`.
The bucket is private, encrypted and versioned; backends enable native S3 locking.
Verify these identities before initializing or recovering AWS state.

## Create the AWS state backend

Backend bootstrap creates only the state bucket and its protections. It is
operated separately from the runtime stages because the bucket must exist before
Terraform can store state in it. Configure and authenticate the AWS profile first.

If the bucket already exists, verify its owner, settings and state keys. Initialize
the owning roots against that backend; do not create another local state for
resources already managed remotely. Missing permissions or a network failure
must not be treated as evidence that a bucket or state key is absent.

For a new bucket:

1. Confirm the configured bucket is absent in the selected account. Review
   `bootstrap/aws/main.tf`, `state.tf` and `backend.tf` for the intended identity.
2. Create a private directory outside Git with `umask 077`. Copy only
   `bootstrap/aws/main.tf`, `bootstrap/aws/state.tf` and
   `bootstrap/aws/.terraform.lock.hcl` into it. Keep `backend.tf` out of this
   initial copy so Terraform starts with local state. Preserve `prevent_destroy`
   and `force_destroy=false`.
3. From that private directory, initialize and review the saved plan:

   ```sh
   terraform init -input=false -lockfile=readonly
   terraform plan -out=bootstrap.tfplan
   terraform show bootstrap.tfplan
   ```

4. Apply the reviewed plan with `terraform apply bootstrap.tfplan`. Copy
   `bootstrap/aws/backend.tf` from the infrastructure checkout into the private
   directory, then run `terraform init -migrate-state`. Review the prompt and
   migrate the bootstrap state into `bootstrap/terraform.tfstate` in S3.
5. Verify that key and the bucket's protections, retain the private local backup,
   and return to the checkout. Initialize `bootstrap/aws` against S3. Continue
   with the [AWS setup guide](../environments/aws/dev/README.md) and its full
   foundation plan.

Bucket creation and state migration are live operations. Configuration validation
uses backend-disabled temporary copies and does not perform these steps.

## Protect private recovery material

Keep state snapshots, their lineage/serial metadata, real `.tfvars`, kubeconfig,
connection Secrets and passwords outside version control. Existing ignored
`.handoff/` files are private recovery material; preserve them during checkout
maintenance. A snapshot is a backup, not a second writable state.

Local kind configuration and database settings use ignored files with checked-in
examples. Database password files and backups live outside the checkout. Follow
[database recovery](../environments/local/docs/database.md#backup-and-restore)
for data restoration; restoring Terraform state does not restore database data.

AWS teardown stores private state/configuration copies and progress records under
`environments/aws/dev/.teardown/<run>/`. Preserve the complete run directory and
matching tool revision until removal or recovery is verified. See
[failure and resume](../environments/aws/dev/docs/teardown.md#failure-and-resume).

## Recover state before applying

Pause applies and other writers while assessing state. Verify the selected
account or cluster, resource IDs, backend key, lineage and serial. Use the latest
authoritative state; never overwrite newer state with an older snapshot or use
another target's state. Keep one active state location per root.

If a state file or key is missing while resources survive, recover a verified
backup or import only resources whose identity and ownership are established.
For local controllers, follow
[platform state recovery](../environments/local/docs/terraform.md#recover-platform-state).
For AWS, inspect bucket versions and private recovery records before choosing a
restore or import procedure. Do not apply a plan proposing duplicate resources
to work around missing bindings.

After recovery, initialize with the committed provider locks and review a fresh
full plan through `scripts/plan.sh` with an explicit environment, stage and
Kubernetes context where required. Keep saved plans private and generate new
ones after state or configuration changes. Check the affected controllers,
workloads and data separately before declaring recovery complete.

Gateway API recovery also requires the Terraform-owned Standard CRD release,
GatewayClass and shared local Gateway. Recover its Helm binding before applying
if the cluster survives; after an authorized rebuild the full platform apply
installs CRDs before Traefik. Restore app HTTPRoutes through GitOps after Secrets
and database probes are ready. Never delete CRDs as a routing rollback.
