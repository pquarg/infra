# Cleanup order and retention choices

This guide summarizes removal scopes for the current foundation/platform/edge
layout. Use [teardown/rebuild](docs/teardown.md) for the automated command and the
[manual retention runbook](https://github.com/pquarg/gitops/blob/main/docs/aws/teardown.md) for reviewed stage plans.

This guide describes how to remove resources defined by this deployment. It
does not record deployment status. Before starting, establish which resources
and Terraform states exist, and choose what data to retain.

## Choose a removal procedure

Use [single-command complete removal](docs/teardown.md) to delete the deployment,
including **all this lab database's snapshots/backups and access logs**.
The state backend is retained by default:

```sh
./scripts/teardown-all.py
```

The script uses private configuration copies, pauses GitOps reconciliation,
checks plans and removal, and retains the backend unless `--delete-backend` is
explicitly selected. That broader scope deletes versioned backend data last using
local bootstrap state. It supports read-only AWS preflight with `--check`,
unattended confirmation with `--yes`, and recovery of an interrupted run with `--resume DIRECTORY`. See its
runbook for prerequisites, scope, and recovery; it is not a project-wide sweeper.

Use the [manual teardown walkthrough](https://github.com/pquarg/gitops/blob/main/docs/aws/teardown.md) when you
want database/log retention choices and to keep the state backend by default.
Its keep-logs branch retains the bucket outside edge state and documents
importing it on a later rebuild. The order below describes this manual procedure.

## Configuration safeguards

The checked-in configuration enables RDS `deletion_protection`,
`prevent_destroy`, a required final snapshot, retained automated backups, and
one-day backup retention. The log bucket has 14-day expiry and
`force_destroy=false`. The separate bootstrap configuration protects the
versioned state bucket with `prevent_destroy` and `force_destroy=false`.

These are configuration defaults, not evidence of existing resources or backups.
Verify actual resources and recovery data before relying on them. Decide what
to retain for each teardown; a previous removal decision does not determine
retention for another deployment.

## Manual cleanup order

Generate fresh, reviewed removal plans for the resources that exist. Do not
reuse saved plans from a previous deployment or teardown. If recovering an
interrupted complete-removal command, use its recorded `--resume` procedure
instead of starting a separate manual cleanup.

1. Decide what database data/snapshots and access logs to retain. Keep the
   state bucket/bootstrap by default in this manual procedure.
2. Remove the CloudFront distribution and log-delivery configuration through a
   reviewed Terraform edge-removal plan. Remove the VPC origin before deleting
   the internal ALB; preserve EKS controllers and networking until then.
3. Deliberately remove/prune the app Ingress through GitOps and wait for the
   load balancer controller to delete its ALB, listeners, target groups, and
   controller-owned security-group resources. Deleting an Argo CD registration
   alone does not ensure app cleanup: the ApplicationSet preserves resources.
4. After the controller-managed ALBs and targets are gone, remove platform's
   two Helm releases while EKS and its AWS prerequisites still exist.
5. Adjust RDS deletion safeguards in a private foundation copy according to the
   chosen backup decision, then review a fresh full foundation destroy plan.
   The log bucket was already retained or deleted during edge removal; keep the
   separate state bootstrap unless its removal is explicitly intended.
6. Verify EKS/workers/disks, RDS and the chosen snapshots/backups, CloudFront
   distribution/VPC origin, ALBs/targets, NAT/EIP, VPC/interfaces, and logs.
   Record retained resources and any remaining charges. Retained storage can
   incur charges even when the application is removed.

Do not destroy platform or foundation before explicit app/ALB cleanup. Terraform
looks up the controller-owned ALB but does not manage its lifecycle.

## State and rebuilding

If the backend is retained, initialize against the existing state bucket and
follow the [staged deployment guide](https://github.com/pquarg/gitops/blob/main/docs/aws/deploy-from-scratch.md).
Reconcile any deliberately retained resources with the configuration before
planning their recreation.

If the backend is removed, follow the
[backend creation procedure](docs/teardown.md#rebuild-after-deleting-the-backend)
before initializing the normal Terraform directories. Do not reuse nonempty
state backups from deleted infrastructure as the state for a new deployment.
