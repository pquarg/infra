# Reaction Explorer deployment warehouse

Run from `infra`. Read [database lifecycle](database.md) first. Use the existing
Compose server and external volume on port 5433. The app checkout's development
warehouse on 5434 remains separate. No cluster recreation, Compose restart or
Terraform database resource is required.

Create a private directory outside every Git working tree (mode 700), with a
`backups` subdirectory. Substitute its absolute path below:

```sh
REACTION_PRIVATE=/absolute/private/reaction-explorer
python3 environments/local/scripts/reaction-explorer-database.py --environment local --password-file "$REACTION_PRIVATE/warehouse-password" --dashboard-password-file "$REACTION_PRIVATE/dashboard-password" provision
python3 environments/local/scripts/reaction-explorer-database.py --environment local --password-file "$REACTION_PRIVATE/warehouse-password" --dashboard-password-file "$REACTION_PRIVATE/dashboard-password" check
```

Provisioning creates missing mode-600 password files, roles `warehouse` and
`dashboard`, and database `reaction_warehouse` owned by `postgres`. Existing
passwords are never rotated; mismatched credentials or an unexpected database
owner stop setup. Keep the original files. Roles have no elevated attributes or
memberships. Other app logins cannot connect to this database. Only `warehouse`
can create source tables and dbt models; `dashboard` gets SELECT privileges,
including default grants for replacement tables in `public` and `analytics`.
A read-only session default adds protection; table grants enforce it even when
that default is overridden. No Airflow database or scheduler is needed here.

Prepare the source schema, pinned workbook and dbt models using the
[GitOps release runbook](https://github.com/pquarg/gitops/blob/main/apps/reaction-explorer/overlays/local/README.md).
Schema and data preparation belong to GitOps/app tooling; infra provisions only
the database and access. Keep warehouse credentials off the web Pods.

## Allow the app through the shared Gateway

The Traefik listener's allowed-namespace selector now includes `reaction-explorer`.
Validate first, then create a full platform plan from `infra`:

```sh
./scripts/validate.sh --all
./scripts/plan.sh --environment local --stage platform --context kind-deployment
```

Review the saved plan before applying it using the command printed by the planner.
This updates the existing shared Helm release. Preserve state, provider locks,
cluster identity, mounts and existing app credentials. Verify existing routes
and the new route after the GitOps release. Follow [platform planning](terraform.md).

## Independent backup and recovery

Pause this app's auto-sync and stop traffic before data changes. Stop any manual
pipeline/Airflow writers targeting this deployment database. Take a private backup:

```sh
python3 environments/local/scripts/reaction-explorer-database.py --environment local --password-file "$REACTION_PRIVATE/warehouse-password" --dashboard-password-file "$REACTION_PRIVATE/dashboard-password" backup "$REACTION_PRIVATE/backups"
python3 environments/local/scripts/reaction-explorer-database.py --environment local --password-file "$REACTION_PRIVATE/warehouse-password" --dashboard-password-file "$REACTION_PRIVATE/dashboard-password" verify-backup /absolute/private/reaction-explorer/backups/reaction-explorer-ARCHIVE.dump
```

Backup uses the restricted warehouse login and writes a mode-600 custom archive.
Verification restores into a uniquely named temporary database, queries source
and modeled tables, and removes only that temporary database, even on failure.
It checks restoration, not the scientific acceptance checks or a browser session.

For actual recovery, provision an empty replacement deployment database with the
original password files, then run `restore ARCHIVE` with the same helper options.
Restore refuses populated `public` or `analytics` schemas, including functions
and types. It retains those empty schemas and restores the archive's
`public` and `analytics` contents transactionally. There is no database reset/drop
operation. Emptying a populated database requires separate destructive authorization.
Rerun `provision` after restore to reapply dashboard/default grants (archives omit
ACLs), then `check`, the GitOps Pod probe, pinned-data reconciliation and browser
checks before resuming traffic/auto-sync. Never restore development `.env` passwords
onto this deployment or overwrite the development volume.

Preserve both password files and private archives independently of the external
Docker volume. After kind replacement the database survives; reload the exact web
image and recreate its namespace/Secret before GitOps sync. Source Git cannot
recover credentials or data. Retain backups deliberately; no automatic expiry is set.

## Verify the helper independently

With Docker Desktop running, execute from `infra/environments/local`:

```sh
python3 tests/reaction-explorer-integration.py
```

This creates a temporary Compose PostgreSQL project, verifies additive retries,
restricted grants, backup and both restore paths, then deletes only that test
project. It does not publish a port or use the deployment database/volume.
