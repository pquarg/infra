#!/usr/bin/env bash
# Verify failures never leave a successful-looking archive or restore over data.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/repo/scripts" "$tmp/repo/database" "$tmp/bin" "$tmp/backups"
cp "$root/scripts/local-database.sh" "$tmp/repo/scripts/"
touch "$tmp/repo/database/.env" "$tmp/repo/database/compose.yaml"
cat > "$tmp/bin/docker" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$LOG"
case "$*" in
  *' config --quiet') exit 0 ;;
  *' pg_dump '*) printf 'archive'; [[ "$CASE" != dump-failure ]] ;;
  *' pg_restore --file=/dev/null'*) cat >/dev/null; [[ "$CASE" != read-failure ]] ;;
  *' pg_restore '*) cat >/dev/null; [[ "$CASE" != restore-failure ]] ;;
  *'SELECT count(*)'*) if [[ "$CASE" == populated ]]; then echo 3; else echo 0; fi ;;
esac
FAKE
chmod +x "$tmp/bin/docker"
fail() { cat "$tmp/out" >&2; printf 'FAIL: %s\n' "$*" >&2; exit 1; }
run() {
  : > "$tmp/log"
  result=0
  PATH="$tmp/bin:$PATH" CASE="$1" LOG="$tmp/log" bash "$tmp/repo/scripts/local-database.sh" "${@:2}" > "$tmp/out" 2>&1 || result=$?
}
for scenario in dump-failure read-failure; do
  run "$scenario" backup "$tmp/backups"
  [[ "$result" != 0 ]] || fail "$scenario must fail"
  [[ -z "$(ls -A "$tmp/backups")" ]] || fail 'incomplete archive retained'
done
run success backup "$tmp/backups"
[[ "$result" == 0 ]] || fail backup
archive="$(find "$tmp/backups" -name '*.dump')"
[[ -n "$archive" && -n "$(find "$tmp/backups" -name '*.dump' -perm 0600)" ]] || fail 'private completed archive'
run populated restore "$archive"
[[ "$result" != 0 ]] || fail 'refuse restore over data'
if grep -q 'pg_restore ' "$tmp/log"; then fail 'must not restore into populated schema'; fi
run restore-failure verify-backup "$archive"
[[ "$result" != 0 ]] || fail 'propagate restore verification failure'
grep -q 'dropdb -U postgres restore_check_' "$tmp/log" || fail 'cleanup disposable verification database on failure'
if grep -q 'dropdb -U postgres myfastapiapp' "$tmp/log"; then fail 'never drop app database'; fi
printf 'Local database backup/restore safety tests passed.\n'
