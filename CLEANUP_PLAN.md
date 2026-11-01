# Cleanup history and future teardown

Use [single-command complete removal](docs/teardown.md) to delete this deployment,
including all database snapshots/backups, access logs and the state backend:

```sh
./scripts/teardown-all.py
```

The script uses private configuration copies, pauses GitOps reconciliation,
checks plans and removal, and deletes the backend last using local state. It
supports read-only `--check`, unattended `--yes`, and `--resume DIRECTORY`.
Offline tests pass; the new command has not executed a live teardown.

Use the [manual teardown walkthrough](../deploy-aws/docs/teardown.md) when you
want database/log retention choices and to keep the state backend by default.
Its keep-logs branch retains the bucket outside root state and documents
importing it on the next rebuild. The manual steps and dated history below
describe that retention-aware procedure, not the complete-removal command.

## Current status — 2026-11-01

The user requested a rebuild after the October 31 teardown. The environment is
now running and public HTTPS/authentication/log delivery are verified. A complete
teardown command was subsequently requested and prepared; it has not been run.
The root Terraform state is populated; an empty-state
check is no longer the expected result. See [README](README.md) and the
[deployment handoff](../deploy-aws/docs/deployment-progress.md).

Current database safeguards are enabled: `deletion_protection`,
`prevent_destroy`, a required final snapshot, retained automated backups and
one-day backup retention. The log bucket has 14-day expiry and
`force_destroy=false`. The prior instruction to delete the old environment
without backups does not authorize discarding data or logs from this rebuild.

## Order for a future cleanup

Prepare a fresh, reviewed removal plan when cleanup is requested. Do not apply
the saved October 31 or November 1 creation/destroy plans; all are consumed/stale.

1. Decide what current database data/snapshots and access logs to retain. Keep
   the state bucket/bootstrap by default, as in the previous teardown.
2. Remove the public distribution and log-delivery configuration through a
   reviewed Terraform edge-removal plan. Remove the VPC origin before deleting
   the internal ALB; preserve the EKS controllers and networking until then.
3. Deliberately remove/prune the app Ingress through GitOps and wait for the
   load balancer controller to delete its ALB, listeners, target groups and
   controller-owned security-group resources. Deleting an Argo CD registration
   alone does not ensure app cleanup: the ApplicationSet preserves resources.
4. Adjust RDS deletion safeguards only according to the chosen backup decision.
   Review a fresh root removal plan after the controller-managed ALB is gone.
5. Remove the remaining root infrastructure. For the log bucket, preserve it or
   explicitly empty it only after the retention decision; Terraform will refuse
   to delete a nonempty bucket. Keep the separate state bootstrap unless its
   removal is explicitly requested.
6. Verify EKS/workers/disks, RDS and the chosen snapshots/backups, CloudFront
   distribution/VPC origin, ALBs/targets, NAT/EIP, VPC/interfaces and logs. Record
   retained resources and any remaining charges. Closing terminals does not
   stop AWS resources.

Do not run root destroy before the explicit app/ALB cleanup. Terraform cannot
manage the lifecycle of the controller-owned ALB merely by looking it up.

## Historical teardown — 2026-10-31

The user explicitly chose deletion without RDS backups and ran Terraform
destroy: **67 resources destroyed**. Read-only checks confirmed an empty root
state; EKS/workers/root disks, RDS/backups/admin Secret, VPC/interfaces/NAT/EIP,
ALBs/targets and cluster log group were absent. CloudFront had not been created.
The separate six-resource state bootstrap and its protected/versioned S3 bucket
were retained. Argo CD's retained-CRD warning required no action after EKS
deletion. Defaults were then restored locally before the November 1 rebuild.

Those checks describe the old deleted environment. They are not the current
running state, and no further old destroy is pending.
