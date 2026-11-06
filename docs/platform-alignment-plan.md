# Align the local and AWS platforms

> Historical alignment record. Application deployment now lives in the single
> [GitOps repository](../../gitops/README.md); old deployment names below describe
> the earlier layout.

Status: proposed implementation plan, prepared 2026-11-06. This document does
not authorize or perform live migration, Terraform applies, state changes,
cluster deletion or database resets.

## Outcome

Use the same responsibilities and operating sequence in both environments:

- Foundation creates the cluster and its underlying connections/resources.
- Terraform installs shared platform software through pinned Helm releases.
- Argo CD deploys application manifests from the appropriate deployment repo.
- AWS has a final edge stage for CloudFront after the app's ALB exists.

Keep all four repositories independent. Use small commits within each owning
repository and coordinated documentation updates across repository boundaries.
Keep kind, Traefik, localhost access and laptop-backed PostgreSQL locally;
keep EKS, the AWS Load Balancer Controller, private ALB, RDS and CloudFront on AWS.
Preserve resource names, app identities, image/chart/provider pins, sync policies,
credentials, database paths, retention settings and network restrictions during
this refactor. Version upgrades and policy changes are separate work.

No new shared framework, fifth repository or local Python requirement is needed.
Existing AWS credential/database/teardown helpers remain in scope only where
their paths or lifecycle assumptions need updating.

## Starting evidence and inventory still needed

All four repositories were clean when this plan was prepared. The local code
already installs Argo CD through Terraform's Helm provider, but the saved local
Argo CD state still contains individual manifest/Secret bindings. The platform
state contains `kubernetes_manifest.traefik`. This establishes saved ownership,
not current live health or the existence of Helm release records.

Before live migration, inspect the explicitly selected kind context and AWS
account/Region, Helm releases, Argo CD Applications/finalizers, controller health,
resource identities and actual state contents. AWS remote state/live inventory
was not queried to prepare this plan. Handle fresh, already-adopted and partially
migrated installations explicitly; do not assume a fresh deployment.

## Target structure and ownership

```text
infra-local/
  kind/                         # Existing kind configuration and mounts
  terraform/platform/           # One local state; Argo CD and Traefik Helm releases
  scripts/                      # kind creation, platform bootstrap, checks

infra-aws/
  bootstrap/                    # Existing protected S3 state-bucket bootstrap
  terraform/foundation/         # VPC, EKS, RDS, controller IAM and frontend SG/rules
  terraform/platform/           # Argo CD and load balancer controller Helm releases
  terraform/edge/               # ALB lookup, CloudFront, functions and access logs
  scripts/                      # Stage checks and lifecycle orchestration

deploy-local/                   # Existing app discovery, manifests and local DB
deploy-aws/                     # Existing app discovery, manifests and RDS connection
```

| Stage | Local owner | AWS owner |
| --- | --- | --- |
| Foundation | kind/Bash: cluster, ports, mount, node label | Terraform foundation: network, EKS, RDS, controller IAM/Pod Identity and ALB frontend SG/rules |
| Platform | Terraform Helm: Argo CD and Traefik | Terraform Helm: Argo CD and AWS Load Balancer Controller |
| Applications | Argo CD from `deploy-local` | Argo CD from `deploy-aws` |
| App load balancer | Traefik routes using app Ingresses | Controller creates ALB/targets from app Ingress; Terraform never owns that ALB |
| Edge | Existing localhost port mapping | Terraform edge: CloudFront VPC origin, distribution, functions and log delivery |

Keep the existing local state location `terraform/platform/terraform.tfstate`
as the final platform state. Retire `terraform/argocd` only after its live
resources have the correct bindings in the final state and recovery is checked.

For AWS, keep the existing S3 bucket and locking/encryption settings. Retain
`dev/terraform.tfstate` as the foundation key to minimize state movement. Use
`dev/platform/terraform.tfstate` and `dev/edge/terraform.tfstate` for the extracted
stages. Keep `bootstrap/terraform.tfstate` unchanged. The directory move alone
does not change a resource's Terraform address or require recreating it.

## Phase 1: establish migration checkpoints and interfaces

1. Record current Git revisions, state addresses, backend keys, cluster contexts,
   Helm release status and object identities. Keep private state/Secret backups
   outside Git; record S3 state version identifiers for AWS.
2. Check the app backup/recovery procedures and record a baseline for database
   authentication, retained data, API readiness, ingress and Git access.
3. Define explicit stage inputs and outputs: cluster name/context, VPC and
   frontend security-group identity, database connection metadata, and public
   endpoint. Keep passwords/tokens outside those outputs and deployment Git.
4. Make cross-stage discovery use small non-secret inputs and AWS data sources
   where practical. Do not give every stage access to an entire sensitive state
   file merely to obtain the EKS endpoint or a VPC ID.
5. Supply the operator allowlist from the same ignored configuration to AWS
   foundation and edge, preserving identical API/CloudFront restrictions.
6. Plan a coordinated pause on infrastructure applies/state writes for each
   ownership transfer. State locks protect individual operations; they do not
   make a multi-state transfer atomic. Record progress so interruption can resume.

Exit condition: an inventory and per-resource source/destination/import-ID map
exist privately, with the desired ownership and expected live changes reviewed.
Do not invent provider import identifiers before inspecting actual resources.

## Phase 2: implement one local Terraform platform

Repository: `infra-local`, with handoff documentation updates in `deploy-local`.

1. Consolidate Argo CD into the final `terraform/platform` root. Keep provider
   and chart pins; keep its current values, Secret behavior and component names.
2. Add `helm_release.traefik` using chart `42.0.0`. Transfer the existing values
   exactly, including NodePort `30080`, IngressClass `traefik`, standard Ingress
   support, disabled dashboard/HTTPS exposure and disabled Traefik CRDs.
3. Replace the Terraform-managed Traefik Application with the Helm release.
   Retire the Kubernetes provider if no resources still require it. Remove
   obsolete registration/rendering paths only after migration support exists.
4. Simplify platform bootstrap to initialize and review one Terraform apply,
   then verify both controllers, Argo CD CRDs, ingress class and kind port mapping.
   Preserve explicit context/kubeconfig selection, state/context guards, reviewed
   applies, and refusal to overwrite an unadopted installation.
5. Update rendering/schema validation, provider locks, Bash safety tests, CI,
   setup and recovery documentation. Keep kind creation and machine-local YAML
   outside Terraform; preserve the selected cluster and laptop data directories.

### Existing local installation migration

Prepare the final configuration before performing these live steps:

1. Back up state, Applications, configuration and credentials; finish active
   syncs/migrations and record any sync policies that need pausing/restoring.
2. Adopt Argo CD directly into the final root. For a manifest installation,
   preserve Secret data and custom settings, compare immutable selectors, and
   plan any necessary controller restarts explicitly. For an existing Helm
   installation, transfer/import its release binding without reinstalling it.
3. Pause the Traefik Application's reconciliation and wait for active syncs to
   finish. Inspect finalizers and remove its registration without cascading
   deletion of its namespace, Deployment, Service, ingress class or other resources.
   Forget only its Terraform binding; never apply a plan that deletes its chart
   workloads as a side effect of retiring the Application.
4. Adopt the running Traefik objects into a real Helm release, preserving names,
   selectors, values and NodePort. Argo CD uses Helm to render templates; it does
   not normally create a Helm release record. Therefore a simple Terraform Helm
   import cannot convert the current Argo CD-managed installation.
5. Once adoption succeeds, import the actual releases into the final platform
   state, retire the old bindings/state directory, and review a fresh plan.
   Routine bootstrap must not enable forced takeover or resource replacement.
6. Avoid automatic uninstall/cleanup-on-failure during first-time adoption of
   existing resources. A failed adoption must retain resources for repair; do
   not uninstall the adopted release as a generic rollback action.
7. Verify Argo CD login/Git access, both controller rollouts, existing app
   registrations, localhost routing, database authentication and retained data.
   Restore recorded sync policies only after these checks pass.

Exit condition: one local state owns two real Helm releases; no Argo CD
Application reconciles Traefik; a new ordinary platform plan has no unexpected
resource changes. The local platform contract and app deployment still work.

## Phase 3: separate AWS Terraform stages

Repositories: `infra-aws`, with path/handoff changes in `deploy-aws`.

1. Move networking, EKS and RDS configuration into foundation while keeping
   resource/module addresses and the existing backend key. Split controller IAM,
   policy attachments and Pod Identity association away from its Helm release;
   keep those AWS prerequisites and frontend SG/rules in foundation.
2. Move the two Helm releases into platform. Discover the existing EKS endpoint
   and CA from the explicitly selected cluster and retain AWS token-based
   authentication. Verify the foundation prerequisites before platform applies.
   Use the same values-file layout and version-pin conventions as local.
3. Move ALB lookup, CloudFront VPC origin/distribution, functions, response
   policies, log bucket and log delivery into edge. Update relative file paths
   and retain the explicit `us-east-1` log-delivery settings. Keep regional
   resources in `eu-north-1` and retain all access restrictions/retention settings.
4. Replace cross-root `module.*`/`depends_on` references with explicit inputs,
   provider data sources and stage readiness checks. Cross-state dependencies
   also require an explicit deletion order; they are not enforced by Terraform's
   dependency graph across separate configurations.
5. Update the deployment handoff to read outputs from the owning roots. Preserve
   the Ingress security-group/ALB identities and document the exact commands.
   New foundations must be plan/apply-able before any app ALB exists; edge is
   planned only after the controller-created ALB is Active with healthy targets.

### Existing AWS state migration

1. Freeze applies from the old root; take private, versioned snapshots of all
   affected states and record resource identities. Check whether each stage is
   deployed, absent or partial rather than treating a missing resource as drift
   to repair automatically.
2. Keep foundation resources in the existing state key and preserve their
   addresses. Extract only platform and edge bindings. For each imported
   resource, validate its provider-specific import ID and expected configuration.
3. Under the apply freeze, forget the source binding without deleting the live
   resource, then import it into its destination state. Perform one audited
   transfer at a time; no infrastructure apply runs in the gap. If import fails,
   stop and repair the transfer or restore the binding to its original owner.
   Do not edit state JSON or overwrite a newer remote state with an old snapshot.
4. Initialize the final directories against their intended keys. Check all
   managed identities occur in exactly one active state. Retire the old root
   configuration/entry point so it cannot recreate moved resources.
5. Run full, untargeted plans for foundation, platform and edge. Imported provider
   defaults may need matching configuration. The expected result is no changes
   to managed AWS resources; investigate unrelated drift as a separate change.

Exit condition: ordinary plans work independently at each valid stage, no
bootstrap `-target` sequence is required, and existing EKS/RDS/ALB/CloudFront
identities, endpoint, credentials and workload behavior are preserved.

## Phase 4: update AWS recovery and teardown before rollout is complete

The existing `infra-aws/scripts/teardown-all.py` copies root-level files and
assumes one runtime state. Splitting Terraform without updating it is incomplete.

1. Make inventory, plan restrictions, state snapshots and resume records aware
   of foundation, platform and edge. Retain bootstrap as a separate final step.
2. Enforce dependency order: remove edge; pause application reconciliation and
   remove app Ingresses; let the still-running load balancer controller clean
   up ALBs/targets; then remove platform; then foundation; then the state backend
   only when its separately selected teardown scope includes it.
3. Preserve existing database/log retention choices, deletion protections,
   private recovery artifacts and rejection of unexpected creates/replacements.
4. Version resume records. Detect legacy one-state runs and require the matching
   old implementation or an explicitly validated conversion; never reinterpret
   an interrupted old run as a new multi-state teardown.
5. Update offline tests for phase order, resource restrictions, failure/resume,
   state/backend selection, controller availability during ALB cleanup and the
   final backend transition. Do not run a destructive teardown to validate this
   refactor. Update teardown/rebuild docs and examples alongside the code.

Exit condition: recovery instructions and tested teardown tooling match the new
layout; no live destroy operation has been used as a configuration check.

## Phase 5: align both deployment workflows

Repositories: `deploy-local` and `deploy-aws`.

1. Provide the same public entry point, `scripts/bootstrap.sh --context CONTEXT
   [--kubeconfig PATH]`, with environment-specific prerequisite checks. Preserve
   existing script names as thin compatibility wrappers during transition.
2. Validate before registration, require an explicit context, select kubeconfig
   consistently, check Argo CD and the relevant ingress controller, apply only
   the existing root Application, and wait for registration synchronization.
3. Clearly distinguish registration completion from app readiness and migration
   success. Keep local automatic sync and AWS manual sync unchanged.
4. Keep existing Application/ApplicationSet names, namespaces, app directories,
   Git URLs and release-image conventions. Align documentation and failure
   messages without renaming live applications or merging repositories.
5. Update `../infra` references to the actual `../infra-aws` checkout and point
   output commands at foundation or edge as appropriate. Review all runbooks,
   not just READMEs, for obsolete paths and state assumptions.
6. Test script ordering, explicit context/kubeconfig forwarding, validation
   failures and controller failures with mocks. Run each repo's schema checks.

Exit condition: both deployment repos expose the same workflow and preserve
their existing app behavior and environment-specific prerequisites.

## Validation and final acceptance

For configuration changes, run:

- `./infra-local/scripts/validate.sh` and `git -C infra-local diff --check`.
- `./deploy-local/scripts/validate.sh` and `git -C deploy-local diff --check`.
- AWS Terraform formatting and backend-disabled init/validate for bootstrap,
  foundation, platform and edge, using committed provider locks.
- Existing AWS function/teardown tests plus new state/ordering regression tests;
  update/add CI validation without deploying resources.
- `./deploy-aws/scripts/validate.sh`, its readiness checks where configuration
  is filled, and `git -C deploy-aws diff --check`.
- Link/path checks in all four repositories.

Configuration validation is followed by separately scheduled live migration and
verification using explicit targets. Check local ingress/API/database health;
on AWS also check ALB target health, HTTPS/authentication, allowed/denied IP
behavior and actual log delivery. Finish with ordinary no-unexpected-change
Terraform plans. Do not claim deployment success from schema checks alone.

Rollback is checkpoint-specific: restore the previous code revision and correct
resource bindings under an apply freeze, preserving the one-owner rule. A Git
revert does not move Terraform state or undo Helm adoption. A transferred
Traefik release must have its new owner detached without uninstalling workloads
before Argo CD ownership can be restored. Retain private backups until all
checks pass; do not reset credentials or rebuild clusters to simplify rollback.

## Delivery order and review boundaries

Deliver configuration, tests, locks and documentation first. Then schedule
state/ownership migrations as separate operator steps with reviewed exact
commands and expected effects. This plan does not execute those steps.

Suggested reviewable batches:

1. Target ownership, interfaces, inventory checklist and migration documentation.
2. Local unified platform configuration, bootstrap, migration guards and tests;
   coordinated local deployment docs.
3. AWS foundation/platform/edge configuration, state-transfer runbook, provider
   locks, validation and deployment output/path updates.
4. AWS teardown/recovery changes and regression tests; required before live AWS
   state cutover completes.
5. Matching deployment entry points, compatibility wrappers and workflow tests.
6. Local migration/verification, then AWS migration/verification, with fresh
   inventory before each and results recorded without private state/credentials.

Commit independently in each repository. No chart/image upgrades, Git pushes,
cluster rebuilds, storage removals, database resets or Terraform destroys are
part of implementing this plan by default. Any live action is reviewed against
the user's authorization and the workspace's execution/data rules.

## References

- [Current local setup](../README.md) and [local adoption guide](terraform.md).
- [AWS architecture](../../infra-aws/docs/architecture.md) and
  [AWS teardown](../../infra-aws/docs/teardown.md).
- [Terraform resource targeting and separate configurations](https://developer.hashicorp.com/terraform/cli/commands/plan#resource-targeting).
- [Terraform state rm](https://developer.hashicorp.com/terraform/cli/commands/state/rm)
  and [resource import](https://developer.hashicorp.com/terraform/language/import).
- [Argo CD non-cascading Application deletion](https://argo-cd.readthedocs.io/en/stable/user-guide/app_deletion/).
