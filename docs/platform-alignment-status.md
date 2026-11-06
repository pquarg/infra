# Platform alignment implementation status

Configuration delivered 2026-11-06 against the
[alignment plan](platform-alignment-plan.md). No live inventory, apply, Helm
adoption, state transfer, cluster rebuild, database reset, destroy or Git push
was performed. Existing ignored state/configuration/data were preserved.

| Repository | Delivered configuration |
| --- | --- |
| `infra-local` | One `terraform/platform` root owning pinned Argo CD and Traefik Helm releases; legacy state/untracked-install guards; one reviewed bootstrap apply; chart/schema validation; adoption/recovery runbooks |
| `infra-aws` | Foundation/platform/edge roots with separate keys and preserved addresses/pins; AWS metadata discovery; shared ignored allowlist input; stage preflight/full-plan helpers; migration ledger/runbook; staged teardown with versioned resumes and optional backend-last deletion; CI |
| `deploy-local` | `scripts/bootstrap.sh --context CONTEXT [--kubeconfig PATH]`, compatibility wrapper, validation/controller/legacy checks before registration, unchanged automatic app sync, workflow tests and handoff docs |
| `deploy-aws` | Same registration entry point/options, compatibility wrapper, validation/controller checks, unchanged manual app sync, workflow tests and current stage/output/recovery paths |

The four original foundation files (network, EKS, database and frontend SG/rules)
were verified unchanged byte for byte. All 28 former top-level AWS resource
addresses occur exactly once across the new roots; existing module addresses
remain unchanged. The backend bucket/bootstrap key and foundation key are retained.
No chart, image, module or provider upgrade was included.

## Configuration checks completed

- Local platform validator: backend-disabled Terraform init/validate with committed
  lock, formatting, Bash safety tests, both chart renders and schemas passed.
  Argo CD: 56 valid resources and 3 intentionally skipped upstream CRD definitions;
  Traefik: 6 valid resources.
- AWS validator: formatting and backend-disabled init/validate for bootstrap,
  foundation, platform and edge passed. All 12 function tests passed. Stage guard
  tests and the final 34 teardown tests passed, including partial installations,
  controller failure, state scopes, versioned resume and optional backend handoff.
- Deployment validators: local 11 resources valid; AWS `--ready` 8 resources valid.
  Both bootstrap suites passed, including context/kubeconfig forwarding, wrappers,
  validation/class/controller failure and registration ordering. The 8 existing
  AWS database/registry helper tests also passed.
- All four repositories passed whitespace checks. Markdown file/directory and
  heading links resolved. Configuration validation used network access for pinned
  upstream dependencies, temporary copies and no cluster/AWS credentials. Helm
  CLI `3.19.0` was checksum-verified in `/tmp` for local chart validation.

## Operator work still required

Before live local migration, inspect actual state/releases/objects, make private
backups and a transfer ledger, verify app backup restoration and record baseline
credentials/data/health/sync policies. Follow
[local adoption](terraform.md#adopt-an-existing-installation), including
non-cascading Traefik detachment and real Helm adoption/import. Do not run ordinary
bootstrap over the legacy installation; its guards deliberately refuse that.

Before AWS migration, establish the apply/state-write freeze, inspect the selected
account/Region and actual state VersionIds/identities, copy the existing allowlist
into ignored `config/operator.tfvars`, and follow the
[state-transfer runbook](../../infra-aws/docs/state-migration.md). Existing resources
must not be recreated to simplify ownership transfer. Partial migrations resume
from the private ledger; old teardown resumes need the matching old implementation.

After each separately authorized migration, verify controller/Git/app/database
health and retained data, localhost routing locally, plus ALB/HTTPS/authentication,
allowed/denied IP behavior and real encrypted log delivery on AWS. Finish with
ordinary plans showing no unexpected resource changes. Only then restore recorded
sync policies and archive retired state privately. Passing the checks above does
not establish successful deployment or live migration.
