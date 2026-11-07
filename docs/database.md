# Local PostgreSQL outside kind

Run from `infra-local`. Docker Compose owns this PostgreSQL instance, separately
from kind and Terraform. Argo CD deploys only the app and its migration Job.
This mirrors the AWS boundary: provision a database, then deploy against its
endpoint. The app repository's Compose database on port 5432 remains an
independent development database.

Existing Kubernetes databases must follow the
[cutover procedure](../../gitops/docs/local/database-migration.md) before publishing
the overlay change. No existing database files are mounted into this container.

## First setup

Start Docker Desktop. This setup targets Docker Desktop on macOS; Linux Engine
needs a separately tested host-routing configuration. Copy the local settings:

```sh
cp -n database/.env.example database/.env
chmod 600 database/.env
mkdir -p "$HOME/.local/share/infra-local/database-secrets"
chmod 700 "$HOME/.local/share/infra-local/database-secrets"
```

Set `LOCAL_DB_SECRETS_DIR` in `database/.env` to that directory's absolute path.
Keep the default `LOCAL_DB_PORT=5433` unless it conflicts with another service.
For first setup only, generate separate admin and app passwords without
overwriting existing files:

```sh
(umask 077; set -o noclobber
 openssl rand -hex 24 > "$HOME/.local/share/infra-local/database-secrets/admin-password"
 openssl rand -hex 24 > "$HOME/.local/share/infra-local/database-secrets/app-password")
./scripts/local-database.sh up
./scripts/local-database.sh status
```

`up` creates the external Docker volume `deployment-project-myfastapiapp-postgres`
if absent, starts PostgreSQL 17.11 and waits for an authenticated app connection.
The init script gives `myfastapiapp` permission to connect and create objects in
the `public` schema, matching the app's AWS grants. It is not a superuser and
cannot create databases or roles. Both API and migrations use this app login.

The server publishes `127.0.0.1:5433`. Host clients use `localhost:5433`; kind Pods
use `host.docker.internal:5433`. This uses Docker Desktop's
[documented host connection](https://docs.docker.com/desktop/features/networking/networking-how-tos/).
No database port is deliberately bound to the laptop's LAN interfaces. Verify the
actual path from a kind Pod before deploying; Docker Desktop, VPN and firewall
settings can affect it. Use the [app setup](../../gitops/apps/myfastapiapp/overlays/local/README.md#provision-credentials)
to provision its connection Secret and run the Kubernetes connectivity probe.

Local connections use `sslmode=disable`; AWS keeps `verify-full` and its RDS CA
mounts. Local does not emulate RDS TLS or managed backups.

## Lifecycle and credentials

```sh
./scripts/local-database.sh stop
./scripts/local-database.sh up
```

Stopping or replacing kind does not stop this database. Stopping Docker Desktop
does. `restart: unless-stopped` restarts it after Docker restarts unless you
explicitly stopped it. Data lives in Docker's external named volume, outside
Git and outside kind. Even Compose `down --volumes` retains this external volume;
Docker disk reset or explicit volume removal can still lose it. Keep independent
backups.

Password files are mounted as Compose secrets. Init scripts and password files
initialize an empty volume only. Editing them does not rotate existing role
passwords. To rotate, pause app sync/writers, use
`./scripts/local-database.sh psql` and the interactive `\password myfastapiapp`
command, then update the app password file and Kubernetes connection Secret.
Restart the API, run the connectivity probe, and verify readiness and login
before re-enabling sync. Rotate the admin login separately with `\password postgres`.
Do not print passwords, connection URLs or a filled `.env` in logs or commit them.

An interrupted first initialization may leave a partially initialized volume.
Inspect container logs and roles before retrying: restarting a populated volume
will not rerun `init-app.sh`. Preserve that volume until recovery or deliberate
disposal is chosen.

## Backup and restore

Pause auto-sync and writers when a consistent cutover/recovery point is required.
Backups contain database data and must remain private and outside Git:

```sh
./scripts/local-database.sh backup
./scripts/local-database.sh verify-backup /absolute/path/to/backup.dump
```

The backup command uses the restricted app login, writes a mode-600 custom-format
archive under `$HOME/.local/share/myfastapiapp/backups`, and checks archive
readability. An optional absolute directory overrides that destination.
`verify-backup` actually restores into a uniquely named disposable database and
drops only that temporary database on exit. It does not alter the app database.
It verifies restoration, not app/schema compatibility or preservation of later writes.

To restore into a freshly provisioned, empty app database with writers stopped:

```sh
./scripts/local-database.sh restore /absolute/path/to/backup.dump
```

This refuses a populated `public` schema and restores in one transaction with
`--no-owner --no-acl`. Objects belong to the restricted app login even when a
legacy backup came from a superuser. It does not restore old cluster roles or
passwords. A nonempty database requires a separately reviewed recovery/reset;
the helper deliberately provides no automatic erase operation. After restoring,
run the app's migration/revision, readiness and existing-login checks.

## Deliberately reset disposable app data

This deletes app accounts, sessions and schema history. Back up and verify a
restore first if the data may be needed. Pause Argo CD app auto-sync, wait for
active syncs/migration Jobs, stop traffic and all writers, and scale the API to
zero using context `kind-deployment` as in the
[cutover runbook](../../gitops/docs/local/database-migration.md#1-pause-and-back-up).
Then open `./scripts/local-database.sh psql` and deliberately run:

```sql
BEGIN;
DROP SCHEMA public CASCADE;
CREATE SCHEMA public AUTHORIZATION postgres;
GRANT USAGE, CREATE ON SCHEMA public TO myfastapiapp;
COMMIT;
```

For recovery, restore a verified archive into that empty schema before syncing.
For a fresh database, sync the entire app to run its selected image's migration
Job and restore the API's declared replicas. Verify revision, readiness, new
registration/login (or existing login after restore), then re-enable auto-sync.
Passwords and the Docker volume are retained. This is independent of cluster
teardown; `teardown-kind.sh --delete-database` targets only legacy laptop files.

## Version changes

Review `database/compose.yaml` independently of app releases. It currently pins
the same PostgreSQL minor version configured in AWS, using the
[official image](https://hub.docker.com/_/postgres). Take and test backups before
upgrades. Never point a different PostgreSQL major at existing files; use an
explicit dump/restore or supported upgrade procedure. Docker volumes do not
replace backups.
