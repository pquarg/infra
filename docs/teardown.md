# Remove the complete learning deployment

This runbook describes the complete-removal procedure without assuming that
the deployment is currently running. Establish which resources and state keys
exist before starting. If a previous run was interrupted, use its `--resume`
procedure below. If resources and the backend have already been removed, no
new teardown run is needed.

From `infra/`, run:

```sh
./scripts/teardown-all.py
```

The command asks you to type project ID `245361883993` once, then runs all stages.
For an explicitly unattended run, use `./scripts/teardown-all.py --yes`.
**This permanently deletes app records, this database's manual snapshots and
retained automated backups, access logs, and the versioned Terraform state bucket.**
It does not keep a final RDS snapshot or AWS-hosted state history.

This is the complete-removal command for this repository's deployment, not an
AWS-project-wide resource sweeper. Shared AWS service-linked roles, resources
created outside this deployment, external GitHub/GHCR credentials, repositories
and images are outside its scope. Local private state/configuration records are
retained for recovery. Billing can still show earlier usage after deletion.

## Before running

- Install Python 3.10+, AWS CLI v2, Terraform 1.17.x and `kubectl`.
- Have a working `aws-project` login in `eu-north-1`. Renew an expired session with
  `aws login --profile aws-project --region eu-north-1`.
- Keep your ignored `eks.auto.tfvars` available. EKS API access must work from
  your current public IP; the script checks cluster access before deletion.
- Run from the machine with access to both existing Terraform state keys. Do
  not run other Terraform commands, app syncs or deployments during teardown.
  Initializing the private working copies may download modules/providers.

To check identity, state ownership, inventory and cluster access without
changing AWS resources:

```sh
./scripts/teardown-all.py --check
```

`--check` creates private local configuration/state records and initializes
Terraform. It does not create removal plans, apply Terraform, scale controllers
or delete anything in AWS. It checks prerequisites; only executing the teardown
can establish that every service accepts its deletion operations.

## What the command does

1. Requires the fixed project, profile and selected Region. Records resource
   IDs, worker disks, NAT Elastic IPs and both remote states under the ignored
   `.teardown/RUN/` directory with private filesystem permissions.
2. In an isolated copy of the Terraform configuration, removes RDS deletion
   safeguards, selects no final snapshot/no retained automated backups, and
   enables emptying the access-log bucket. A settings-only targeted plan avoids
   deploying unrelated configuration changes.
3. Removes both CloudFront functions if present, the distribution, VPC origin,
   log delivery and log bucket through a normal configuration-removal plan.
   EKS, RDS, controllers and networking remain running at this checkpoint.
4. Scales Argo CD's application and ApplicationSet controllers to zero, so
   reconciliation cannot recreate an Ingress. Removes Ingresses and LoadBalancer
   Services in this dedicated cluster directly through Kubernetes. Waits for
   the load balancer controller to remove the VPC's ALBs and target groups.
   This does not require Git commits, Git pushes or Argo CD UI actions.
5. Removes a detached, controller-tagged backend security group if one remains.
   Only the controller's known shared target-group ingress rules can be revoked;
   unexpected references or attached interfaces stop the command.
6. Destroys the remaining root infrastructure through a fresh saved Terraform
   destroy plan. Deletes any older manual snapshots and retained automated
   backups belonging to `deployment-project-dev-postgres`.
7. Verifies root state is empty and checks CloudFront/origin, EKS, workers/root
   disks, RDS/recovery data/admin Secret, load balancers/targets, NAT/EIPs,
   VPC/interfaces, cluster logs and the access-log bucket. Authorization or
   connectivity errors are failures, never evidence of deletion.
8. Confirms root state is still empty and bootstrap state has not changed.
   Copies the latest bootstrap state into a separate **local-backend** working
   directory. Enables emptying the versioned state bucket, then destroys the
   bootstrap resources and verifies the bucket is absent. Terraform does not
   delete the bucket while using that bucket as its active backend.

Each plan is checked before apply. Creation, replacement and unrelated updates
are rejected. Temporary edits affect only the private copies; committed/default
RDS and bucket protections remain enabled in the source configuration. No old
saved plan is reused. Plan checks can stop for configuration drift: inspect the
reported plan and reconcile the cause rather than bypassing the checks.

## Interruption or failure

The command stops on an error and prints the recovery directory. After fixing
the blocker or renewing credentials, resume the **same** run:

```sh
./scripts/teardown-all.py --resume .teardown/RUN
```

Use the actual path printed by the command. Completed phases are skipped, and
the failed phase generates fresh plans. This includes resuming after the remote
backend is deleted: the final stages use the saved local bootstrap state.
Starting a new run after a partial teardown can lose the original inventory;
use `--resume` instead. A finished run's record is historical evidence; repeating
it does not inventory resources created afterward.

Argo CD may remain paused after a failure. Its original controller replica
counts are in `progress.json`. Keep it paused while teardown is incomplete.
If abandoning teardown, first assess what remains; restoring replicas can
recreate an Ingress, and a normal Terraform apply can recreate infrastructure.
The script does not bypass Kubernetes finalizers, forget resources from state,
or delete unexpected security-group references to force progress.

The private directory contains sensitive state, operator configuration and
plans. Protect it, keep it out of Git, and remove it securely when no longer
needed. Source files and GitOps manifests do not need restoration after this
command because it never edits them.

## Rebuild after deleting the backend

The normal staged deployment guide assumes the state bucket exists. After
complete removal, recreate it first. In a private copy of `bootstrap/` without
`backend.tf`, initialize a local backend, review a fresh plan and apply it to
create the protected state bucket. Add the original S3 backend configuration
and use `terraform init -migrate-state` to move that new bootstrap state to
`bootstrap/terraform.tfstate` in S3. Keep any local state private. Initialize
the original repository directories against the recreated S3 bucket, then
follow the [staged rebuild guide](../../deploy-aws/docs/deploy-from-scratch.md).
Do not reuse the deleted environment's nonempty state backups as new live state.

For a manual teardown that **retains** the state backend and offers database/log
retention choices, use [the deployment runbook](../../deploy-aws/docs/teardown.md).

## Local validation

The command has offline regression tests for plan restrictions, phase ordering,
failure/resume, AWS error handling, configuration isolation, and the switch to
local bootstrap state. They run without invoking AWS, Kubernetes, or Terraform.
They do not establish whether resources currently exist or whether AWS will
accept a particular deletion; each teardown must pass its own preflight, plan
checks, and removal verification.

```sh
python3 -m unittest discover -s tests -p 'test_teardown_all.py' -v
```

References: [EKS deletion prerequisites](https://docs.aws.amazon.com/eks/latest/userguide/delete-cluster.html),
[RDS deletion and recovery choices](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_DeleteInstance.html),
[Terraform backend initialization](https://developer.hashicorp.com/terraform/cli/commands/init#backend-initialization).
