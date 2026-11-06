# AWS teardown and rebuild

Destructive work requires a separately chosen scope. No teardown is a validation
check. The staged command intentionally deletes runtime resources, **all this
lab database's snapshots/backups**, and CloudFront access logs. It retains the
state backend by default. For data/log retention use the
[manual deployment runbook](../../deploy-aws/docs/teardown.md) instead.

```sh
# Read-only preflight/inventory; writes private local artifacts only.
./scripts/teardown-all.py --check
# Destructive runtime/data/log removal after typed project confirmation:
./scripts/teardown-all.py
# Explicit broader scope, also deleting all versioned backend data last:
./scripts/teardown-all.py --delete-backend
```

`--yes` skips typed confirmation and has the same destructive meaning. It must
be deliberately selected. Do not use it for routine checks. The CLI requires
Terraform, AWS CLI v2, kubectl, Python 3, the configured profile/account/Region,
and ignored `config/operator.tfvars`. Finish the [state transfer](state-migration.md)
first. Misplaced stage bindings and legacy records are rejected before deletion.

## Stage order and private artifacts

The command uses mode-700 `.teardown/<run>/` with private copies at
`terraform/foundation`, `terraform/platform`, `terraform/edge` and bootstrap.
Relative values/function/policy paths and provider locks remain intact. It reads
all three intended backend keys independently, snapshots states/outputs into
private files and records S3 VersionIds. Missing stage keys are absent stages;
permission/expired-token/network errors never mean absence. Source configuration,
ignored variables and normal provider directories are not edited.

The order is enforced across state boundaries:

1. Inventory foundation/platform/edge and bootstrap, verify account/resource
   scope and record EKS/RDS/VPC/CloudFront/data identities.
2. In private copies only, permit the chosen full DB/log deletion. Settings plans
   allow only the existing deletion flags; no drift, creates or replacements.
3. Destroy edge and verify CloudFront distribution/VPC origin removal.
4. Verify the load balancer controller is running. Pause both Argo CD reconcilers,
   wait for their Pods to stop, delete Ingresses/LoadBalancer Services, wait for
   ALBs/targets to disappear, then handle only known controller backend groups.
5. Destroy platform's Helm releases, then foundation. Each destroy plan rejects
   creates/replacements/updates and resources outside its stage.
6. Delete this database's remaining snapshots/retained backups; verify runtime,
   worker disks, EIPs, logs, network and recovery data are gone.
7. **Only with `--delete-backend`**, recheck all runtime states are empty, snapshot
   current bootstrap and reject concurrent serial/lineage changes. Switch the
   bootstrap private copy to local state, permit bucket emptying, then destroy
   and verify the backend. Never destroy a bucket from the S3 state stored in it.

The command retains recorded Argo CD replica counts for interrupted recovery.
It does not remove shared service-linked roles or resources outside this lab.
Unexpected references/finalizers or AWS constraints stop the run for inspection;
they are not bypassed by broad deletion. S3 locks protect operations, and a local
command lock prevents two invocations in this checkout. Freeze other applies.

## Failure and resume

Progress records have version `2`, explicit state keys and backend scope. A
missing/legacy version is rejected: use the old implementation at its recorded
Git revision for a legacy run or perform a separately validated conversion.
Never reinterpret its completed phases as staged teardown phases.

Fix the blocker and use the printed resume command with the original scope:

```sh
./scripts/teardown-all.py --resume /absolute/private/run
# If the original run included backend deletion:
./scripts/teardown-all.py --delete-backend --resume /absolute/private/run
```

Completed steps are not repeated; failed steps retry with private configuration
and state refreshes. Do not change source configuration halfway through a run or
run normal applies during partial teardown. Inspect Helm failures before retry;
GitOps may remain paused. Retain private state/configuration/kubeconfig and
credential backups until removal/recovery is verified. A Git revert does not
restore remote state or a deleted database.

## Rebuild after deleting the backend

If the backend survived, initialize each root against its existing key and follow
[the deployment walkthrough](../../deploy-aws/docs/deploy-from-scratch.md). Do not
use consumed plans or invent fresh local state over an existing bucket/state.
If the backend was explicitly removed, create a new private local bootstrap copy:

1. Verify the expected bucket is absent in the selected account, and inspect
   private removal records. An access error does not mean it is absent.
2. Copy `bootstrap/*.tf` and its provider lock to a private directory outside Git,
   excluding `backend.tf`, old `.terraform` and old state. Keep checked-in safe
   defaults (`prevent_destroy=true`, `force_destroy=false`).
3. In that directory run `terraform init -lockfile=readonly`, review `terraform
   plan -out=bootstrap.tfplan`, then apply the reviewed plan. This creates only
   the protected bucket and its settings.
4. Add the checked-in `bootstrap/backend.tf` to that private copy and run
   `terraform init -migrate-state`. Review the migration prompt, verify the
   `bootstrap/terraform.tfstate` key, then initialize the normal bootstrap/root
   stage directories. Keep the private local backup.
5. Review fresh full stage plans in lifecycle order. Restoring a database is a
   separate choice; creating a new instance does not recover deleted data.

## Validation

```sh
./scripts/validate.sh
```

Offline tests cover plan scopes, phase ordering, failure/resume, missing stages,
state/backend selection, controller availability, legacy record rejection and
local bootstrap handoff. Backend-disabled configuration checks cannot prove live
removal/readiness. Never run a destroy to test this refactor.
