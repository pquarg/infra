# Dedicated job tracker database

Run from `infra`, targeting only the existing local Compose PostgreSQL. Read
[database lifecycle](database.md) first. No Terraform apply, PostgreSQL restart,
volume replacement or demo-api credential change is needed. The Compose server
keeps its existing name, port, volume and mount sources.

Choose an absolute private directory **outside every Git working tree** for the
tracker password and backups. Create it with mode 700. The path below is an
example shell variable; substitute your own directory each session:

```sh
TRACKER_PRIVATE=/absolute/private/job-tracker
mkdir -p "$TRACKER_PRIVATE/backups"
chmod 700 "$TRACKER_PRIVATE" "$TRACKER_PRIVATE/backups"
python3 environments/local/scripts/job-tracker-database.py --environment local --password-file "$TRACKER_PRIVATE/password" provision
python3 environments/local/scripts/job-tracker-database.py --environment local --password-file "$TRACKER_PRIVATE/password" check
```

`provision` creates a mode-600 password file only if missing, creates only missing
`job_tracker` role/database, then verifies login identity and restricted privileges.
It never alters an existing password. Keep the original file for an existing role;
a mismatch fails rather than rotating credentials. An unexpected database owner
also stops setup for inspection. Passwords travel over stdin, not command arguments
or routine logs. This helper uses the existing Compose `.env` and admin socket.

The database is owned by `postgres`; the tracker login has CONNECT and
USAGE/CREATE on its public schema, without superuser, role/database creation,
replication, bypass-RLS or inherited memberships. PUBLIC cannot connect to the
tracker database. The tracker has no grants on demo tables; existing demo grants
and credentials remain unchanged. Application Alembic migrations create tracker
tables as the restricted login. The helper checks DDL inside a rolled-back
transaction before release.

Use this password for the private GitOps connection Secret, with
`host.docker.internal:5433/job_tracker` from kind Pods, and verify the actual
Pod path with GitOps `check-job-tracker-local.py`. A successful host-side check
alone does not establish Pod connectivity. See the
[local release runbook](https://github.com/pquarg/gitops/blob/main/apps/job-tracker/overlays/local/README.md).

## Independent backup and restore

Tracker originals, extracted evidence, accounts, session/recovery hashes, profiles,
research, decisions and notes all live in this database. No records are copied
from the app development/test databases. Protect archives as private documents.
Stop traffic and API/worker writers for a release recovery point, and pause
`job-tracker` auto-sync while doing so. Leave demo-api running.

```sh
python3 environments/local/scripts/job-tracker-database.py --environment local --password-file "$TRACKER_PRIVATE/password" backup "$TRACKER_PRIVATE/backups"
python3 environments/local/scripts/job-tracker-database.py --environment local --password-file "$TRACKER_PRIVATE/password" verify-backup /absolute/private/job-tracker/backups/job-tracker-ARCHIVE.dump
```

The custom-format backup uses only the tracker login/database, mode 600 and an
atomic rename after archive readability succeeds. Failures remove partial files.
Verification restores transactionally into a uniquely named `jt_restore_check_*`
database, checks the expected schema revision and account count, and drops only
that verification database, including on failure. It never drops the tracker or
demo database. This checks restoration/schema, not external provider quality;
check existing login, originals and decisions after an actual recovery too.

For actual recovery, stop writers and provision an **empty** tracker database
with the original password file. Restore before allowing automatic migrations:

```sh
python3 environments/local/scripts/job-tracker-database.py --environment local --password-file "$TRACKER_PRIVATE/password" restore /absolute/private/backup.dump
```

Restore refuses any populated public schema, including one containing only
functions. It provides no reset/drop operation. Clearing existing data requires
separate destructive authorization and a reviewed recovery plan. Restore uses
`--no-owner --no-acl --single-transaction --exit-on-error`, retaining ownership
under the restricted tracker login. Cluster roles/passwords and provider keys
are not in these dumps; recover their private configuration separately.

Verify Alembic head, readiness, existing account login, uploads and decisions
before resuming writers/traffic and auto-sync. A rollback to older app images
requires compatible schema; it never reverses a migration.

Backups are operator-managed, with no automatic expiry. Retain only the private
copies you intend to keep, and record/delete expired copies explicitly. Deletion
from the live app does not erase archives/snapshots or recall third-party calls.
Restoring an older backup can resurrect subsequently deleted accounts/documents;
reconcile those deletions before processing resumes. Losing both the external
volume and backups loses private data; source Git is not recovery material.
