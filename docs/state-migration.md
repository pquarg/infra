# Split existing AWS state ownership

This runbook prepares a separately authorized live migration. The configuration
refactor has not queried AWS state or moved any live binding. Do not apply any
new root until this inventory is complete. Freeze all infrastructure applies
and state writes for the whole transfer, including CI and other operators.
Individual Terraform locks do not make a multi-state transfer atomic.

## Inventory and checkpoint

Record Git revisions for all four repos, AWS account `245361883993`, profile
`aws-project`, Region `eu-north-1`, cluster `deployment-project-dev` and the
explicit kubeconfig/context. Check identity using `aws sts get-caller-identity
--profile aws-project --region eu-north-1`. Keep private backups outside Git
with `umask 077`. Record baseline Git access, controller/Helm health, Argo CD
Applications/finalizers/policies, ingress, ALB/target health, database
connectivity/authentication and retained records. Verify app backup/restore
procedures before any controller restart.

Initialize the new roots against the **existing** state bucket, never migrate
or copy old root state wholesale to a new key:

| Directory | S3 key | Resources |
| --- | --- | --- |
| `bootstrap` | `bootstrap/terraform.tfstate` | Existing protected state bucket, unchanged |
| `terraform/foundation` | `dev/terraform.tfstate` | Existing VPC/EKS/RDS/modules, controller IAM/Pod Identity and frontend SG/rules |
| `terraform/platform` | `dev/platform/terraform.tfstate` | `helm_release.argocd`, `helm_release.load_balancer_controller` |
| `terraform/edge` | `dev/edge/terraform.tfstate` | `aws_cloudfront_*`, `aws_cloudwatch_log_delivery*`, `aws_s3_bucket*.cloudfront_logs` |

The foundation directory move keeps every retained resource address and the old
backend key. The old repository root has no Terraform configuration; do not
reuse its `.terraform` backend cache for applies. Keep that cache privately for
recovery until cutover is verified. The `bootstrap` directory is unchanged.

Copy the existing operator allowlist, without changing it, into ignored
`config/operator.tfvars` (see `config/operator.tfvars.example`). Both foundation
and edge plans use this exact file; do not maintain independent copies. Keep
old ignored `eks.auto.tfvars` until its content is verified and archived privately.
Do not put passwords or tokens in stage inputs or Git.

```sh
umask 077
terraform -chdir=terraform/foundation init -lockfile=readonly
terraform -chdir=terraform/platform init -lockfile=readonly
terraform -chdir=terraform/edge init -lockfile=readonly
terraform -chdir=terraform/foundation state list
terraform -chdir=terraform/platform state list
terraform -chdir=terraform/edge state list
```

Pull each existing state into a private snapshot and record its lineage/serial,
S3 key and S3 object VersionId. A nonexistent stage key is an **absent stage**, not
a reason to create or import resources. `aws s3api head-object --bucket
 deployment-project-tfstate-245361883993-eu-north-1 --key KEY --profile aws-project
 --region eu-north-1` records state metadata. Distinguish a missing key from
permission/network errors. Also snapshot the bootstrap state. Do not expose
`terraform state show`, Helm values or Secret backups in logs or commits.

Build a private per-resource ledger from actual inventory:

```text
source key | source address | live identity | destination key/address |
provider import ID verified against pinned provider docs | status |
source/destination snapshot/serial/VersionId | verification result
```

Classify each resource as fresh/absent, still in source, already transferred or
partially transferred. Helm release IDs must come from the real cluster release
records; do not invent CloudFront/log-delivery provider import IDs from names.
Inspect the pinned AWS provider's import documentation for **each** resource,
including regional log delivery in `us-east-1`. Data sources are re-read, not
imported or transferred. Never import the controller-owned ALB or its targets.

## Transfer one binding at a time

Keep addresses unchanged in their new roots. For only a resource actually still
owned by foundation, with its import ID verified in the ledger, the operator
sequence is:

```sh
# Set these from the reviewed private ledger, not a guessed identifier.
terraform -chdir=terraform/foundation state rm -dry-run "$ADDRESS"
terraform -chdir=terraform/foundation state rm "$ADDRESS"
terraform -chdir="terraform/$DESTINATION" import "$ADDRESS" "$IMPORT_ID"
```

For edge imports add `-var-file=../../config/operator.tfvars` **before** the
address. Keep the foundation source configuration available from its recorded
old revision for rollback; a source re-import must use that configuration,
not a root in which its resource block no longer exists. Do not apply the old
monolithic root after starting transfer.

After each transfer inspect both states and record the new serials/VersionIds.
If import fails, stop: repair the destination import or restore the binding to
its original owner using the recorded old configuration under the same freeze.
Never apply while a resource is unbound, edit state JSON, disable locks or push an
old snapshot over newer remote state. Do not copy the complete sensitive state
to platform/edge just to access cluster metadata; they use AWS data sources.

For an already-adopted resource verify identity and skip transfer. For an absent
resource, document that it is absent and plan its intended creation separately.
A partial migration resumes from the ledger and current state, not by repeating
`state rm` indiscriminately. No infrastructure applies run during these gaps.

## Verify before releasing the freeze

All managed identities must occur in exactly one active state. Foundation must
contain no Helm or edge bindings; platform contains exactly its two real Helm
releases; edge contains only its intended resources. Preserve existing EKS,
RDS, SG, ALB, CloudFront, log delivery, endpoint and credential identities.
Inspect imported provider defaults and match configuration deliberately.

Run full, untargeted plans using `scripts/plan.sh` for each valid deployed stage.
Expected result for existing managed AWS resources: **no changes**. Diagnose
unrelated drift separately; do not bundle chart/image/provider upgrades or
allowlist changes into cutover. Check app sync policy remains manual, controller
rollouts/CRDs, Git access, `/health`, `/ready`, HTTPS redirects, login/logout,
ALB healthy targets, allowed and denied IP behavior and actual encrypted log
arrival. Record results without secrets. A schema check is not live acceptance.

Retire old entry points only once state ownership and recovery are verified.
The staged teardown tooling must be validated before AWS cutover completes.
Rollback restores previous code plus the correct bindings under the same freeze;
a Git revert does not transfer state. Keep private snapshots until all checks pass.

References: [state rm](https://developer.hashicorp.com/terraform/cli/commands/state/rm),
[resource import](https://developer.hashicorp.com/terraform/language/import),
[stage lifecycle](architecture.md), [teardown and recovery](teardown.md).
