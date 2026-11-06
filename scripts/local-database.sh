#!/usr/bin/env bash
set -euo pipefail
usage() {
  printf 'Usage: %s up|status|stop|backup [backup-directory]|verify-backup ARCHIVE|restore ARCHIVE|psql\n' "${0##*/}"
  printf 'Local Compose PostgreSQL only. restore requires an empty public schema.\n'
}
if (( $# == 0 )); then usage >&2; exit 1; fi
action="$1"; shift
case "$action" in
  up|status|stop|psql) (( $# == 0 )) || { usage >&2; exit 1; } ;;
  backup) (( $# <= 1 )) || { usage >&2; exit 1; } ;;
  verify-backup|restore) (( $# == 1 )) || { usage >&2; exit 1; } ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 1 ;;
esac
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$root/database/.env" ]]; then
  printf 'Configure database/.env and password files first; see docs/database.md.\n' >&2
  exit 1
fi
compose=(docker compose --env-file "$root/database/.env" -f "$root/database/compose.yaml")
"${compose[@]}" config --quiet
app_client=("${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/app_password)"; exec "$@"' sh)
case "$action" in
  up)
    docker volume create deployment-project-myfastapiapp-postgres > /dev/null
    "${compose[@]}" up -d --wait --wait-timeout 120 postgres ;;
  status) "${compose[@]}" ps ;;
  stop) "${compose[@]}" stop postgres ;;
  psql) "${compose[@]}" exec postgres psql -X -U postgres -d myfastapiapp -v ON_ERROR_STOP=1 ;;
  backup)
    backup_dir="${1:-$HOME/.local/share/myfastapiapp/backups}"
    if [[ "$backup_dir" != /* || "$backup_dir" == "$root/.." || "$backup_dir" == "$root"/* ]]; then
      printf 'Choose an absolute backup directory outside the repository.\n' >&2; exit 1
    fi
    umask 077
    mkdir -p -- "$backup_dir"
    backup_dir="$(cd -- "$backup_dir" && pwd -P)"
    if git -C "$backup_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      printf 'Backups must be outside Git working trees.\n' >&2; exit 1
    fi
    archive="$(mktemp "$backup_dir/myfastapiapp-compose-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
    trap 'rm -f -- "$archive"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    "${app_client[@]}" pg_dump -h 127.0.0.1 -U myfastapiapp -d myfastapiapp --format=custom --no-owner --no-acl > "$archive"
    [[ -s "$archive" ]]
    "${compose[@]}" exec -T postgres pg_restore --file=/dev/null < "$archive"
    mv -- "$archive" "$archive.dump"
    printf 'Backup: %s.dump\nRun verify-backup with this archive to test an actual restore.\n' "$archive" ;;
  verify-backup)
    [[ -f "$1" ]] || { printf 'Archive not found.\n' >&2; exit 1; }
    # A new database, never the app database. Preserve original on any failure.
    scratch="restore_check_$(date -u +%Y%m%d%H%M%S)_$$"
    "${compose[@]}" exec -T postgres createdb -U postgres --owner=myfastapiapp "$scratch"
    trap '"${compose[@]}" exec -T postgres dropdb -U postgres "$scratch"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    "${app_client[@]}" pg_restore -h 127.0.0.1 -U myfastapiapp --dbname="$scratch" --no-owner --no-acl --exit-on-error --single-transaction < "$1"
    printf 'Actual restore into disposable database succeeded. Check app behaviour after cutover too.\n' ;;
  restore)
    [[ -f "$1" ]] || { printf 'Archive not found.\n' >&2; exit 1; }
    objects="$("${app_client[@]}" psql -h 127.0.0.1 -U myfastapiapp -d myfastapiapp -X -Atqc "SELECT count(*) FROM pg_depend WHERE refclassid = 'pg_namespace'::regclass AND refobjid = 'public'::regnamespace")"
    [[ "$objects" == 0 ]] || { printf 'Refusing restore: public schema is not empty.\n' >&2; exit 1; }
    "${app_client[@]}" pg_restore -h 127.0.0.1 -U myfastapiapp --dbname=myfastapiapp --no-owner --no-acl --exit-on-error --single-transaction < "$1"
    printf 'Restored app objects as the restricted app login. Verify revisions and existing data before traffic.\n' ;;
esac
