#!/usr/bin/env bash
# Runs only when the official image initializes an EMPTY volume.
set -euo pipefail
APP_PASSWORD="$(cat /run/secrets/app_password)"
[[ -n "$APP_PASSWORD" ]] || { printf 'App password must not be empty.\n' >&2; exit 1; }
export APP_PASSWORD
psql -X --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_PASSWORD
CREATE ROLE myfastapiapp LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'app_password';
GRANT CONNECT ON DATABASE myfastapiapp TO myfastapiapp;
GRANT USAGE, CREATE ON SCHEMA public TO myfastapiapp;
SQL
unset APP_PASSWORD
