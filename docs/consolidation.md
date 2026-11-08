# Consolidation handoff

The `infra` repository is the configuration and operating checkout. The workspace
root remains outside Git. Application source and GitOps remain separate repos.
Paths below are relative to the `infra` repository root.
No provisioning, apply, destroy, backend migration or live AWS access was used
for this refactor.

## Provenance

Both source repositories were clean on branch `main`. Non-squashed subtree
imports preserve their complete committed histories as ancestors of `infra/main`:

| Source | Imported commit | Initial import prefix |
| --- | --- | --- |
| `infra-local` | `47d109d4b6a3462a63e990699aa61932530694c3` | `environments/local` |
| `infra-aws` | `b72a607713e0506a1f38bed023457055758bb781` | `environments/aws/dev` |

The original branches/remotes, `.repo-backups/`, database volumes, external
credentials and AWS teardown records remain in place. Ignored `INACTIVE.md`
notices in the source checkouts identify them as recovery references. Do not
operate normal plans/applies there. The new private GitHub repository is
[`pquarg/infra`](https://github.com/pquarg/infra), configured as `origin` for this
checkout. Consolidation changes are committed and pushed in each owning
repository: infrastructure here, GitOps handoff changes in `pquarg/gitops`, and
application documentation in `pquarg/myfastapiapp`. The workspace README and
consolidation plan remain local because the workspace root is outside Git.
Retirement or archival of the old remotes remains a separate action.

## Local state and settings

The authoritative writable local state is
`environments/local/platform/terraform.tfstate`. It was transferred byte-for-byte
from `../infra-local/terraform/platform/terraform.tfstate`, retaining lineage,
serial, `kind-deployment` output and both Helm release addresses. No Terraform
state command changed any binding. The original operating state path is now
absent, so there are no two writable operating copies. Never initialize/apply
from the inactive checkout as if it were a fresh installation.

A read-only snapshot remains at
`../infra-local/.generated/consolidation-20261108/terraform.tfstate`; another
private snapshot and the hash/lineage/serial handoff ledger are in ignored
`.handoff/`. These snapshots are backups, not alternate active states. Rollback
must use the latest authoritative state, with a coordinated freeze and only one
active location; never restore an older snapshot over newer state.

The existing ignored kind configuration and database `.env` were copied with
permissions and contents preserved. External secrets paths are unchanged.
No real local platform `.tfvars` or current AWS `config/operator.tfvars` existed
to copy. Prepare AWS operator settings from their example before future AWS work;
legacy monolithic variables and saved plans were retained in the original checkout.
No `.terraform` directory, saved plan, generated archive or old bootstrap state
was imported into the active roots. Initialize clean provider directories with
the committed locks before future planning.

Compose still targets the same project/volume and passwords. Its running
container can retain its original init-script bind path; keep the old checkout
available. No database restart/recreation was needed or performed. See
[database checkout changes](../environments/local/docs/database.md#checkout-consolidation).

## AWS state and interrupted teardown

AWS keys, account/profile/regions and resource identities are unchanged. No remote
backend was queried or initialized. Missing AWS infrastructure/backends stay
absent; local bootstrap snapshots do not prove a live backend exists. Live AWS
planning requires a separately scoped AWS task and identity/backend verification.

Old `.teardown` records remain under `../infra-aws/.teardown`. Resume using the
original checkout and the tool revision that created the record, with the original
scope. Some historical records predate the staged tool: do not assume today's
source tool supports every old record. The preserved Git history provides prior
tool revisions. Keep snapshots unchanged and do not run a new teardown merely
to validate consolidation. New runs use record version 3 and preserve the new
repository-relative assets, common values, backends and operator-variable paths.
See [teardown recovery](../environments/aws/dev/docs/teardown.md#pre-consolidation-records).

## Verification

The source baseline recorded a missing Helm executable for local validation and
network-blocked Terraform dependency resolution for AWS; the existing AWS safety
and function tests passed. Subsequent configuration checks with network access
and the pinned Helm passed:

- `./scripts/validate.sh --all` passed for both targets after retrying a
  transient upstream chart-download HTTP 504. The target-specific checks cover:
- `./scripts/validate.sh --environment local`: Terraform, Compose configuration,
  Helm renders/schema checks and database/bootstrap/teardown safety tests.
- `./scripts/validate.sh --environment aws/dev`: all four Terraform roots,
  AWS stage guards, CloudFront function tests and 35 teardown tests.
- `../gitops/scripts/validate.sh --ready`: both overlays, schemas and helper tests.
- Eight public dispatch tests cover root/backend isolation, invalid target/stage
  rejection, wrong account/context/state, one-stage plans and validation dispatch.
- Before/after Argo CD renders at chart `10.11.6` are byte-for-byte identical
  for both environments. All five provider locks are byte-for-byte unchanged;
  Terraform source differences are limited to paths and the common values input.
- Private-path ignore checks, documentation paths/links and whitespace checks.

Publication checks on 2026-11-08 reran `./scripts/validate.sh --all` and
`../gitops/scripts/validate.sh --ready` successfully with network access, the
temporary pinned Helm binary and a temporary Terraform provider cache. Relative
documentation paths, Markdown heading anchors and whitespace checks also passed
across infrastructure, GitOps and application documentation.

Detailed private provenance, settings/state handoff metadata and validation logs
are stored under `.handoff/`, which must remain outside Git. Helm `v3.19.0` was
downloaded and checksum-verified into a temporary tools directory; it was not
installed globally. Install the documented Helm prerequisite for later local
validation, or use that temporary tool while it remains available.

Although the rendered objects are identical, changing the Helm `values` inputs
may appear as an in-place release update in a fresh Terraform plan. Review the
actual plan before any apply; render equality alone cannot prove a no-op plan.

No fresh live plans, controller/workload health checks, database connectivity or
application-data checks were performed. The optional Docker database integration
test was not run. These remain separate live acceptance work; passing the checks
above does not establish deployment success or runtime equivalence. Future changes
must be reviewed and published separately in each owning repository. Argo CD
continues reading the unchanged GitOps remote, overlays and sync policies.
