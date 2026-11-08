#!/usr/bin/env bash
# Exercise destructive paths using fake tools only; never contact Docker/Kubernetes.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/workspace/infra-local/scripts" "$tmp/workspace/infra-local/terraform/platform"
cp "$root/scripts/teardown-kind.sh" "$tmp/workspace/infra-local/scripts/"
printf 'retained state\n' > "$tmp/workspace/infra-local/terraform/platform/terraform.tfstate"
cat > "$tmp/bin/fake" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s' "${0##*/}" >> "$LOG"
printf ' <%s>' "$@" >> "$LOG"
printf '\n' >> "$LOG"
case "${0##*/}" in
  docker)
    if [[ "$CASE" == docker-failure ]]; then exit 41; fi
    [[ "$*" == info ]] || exit 94
    ;;
  kind)
    case "$*" in
      'get clusters')
        echo other-cluster
        if [[ "$CASE" != absent && ! -f "$REMOVED" ]]; then echo example; fi ;;
      'get nodes --name example') printf 'example-worker\nexample-control-plane\n' ;;
      'delete cluster --name example')
        if [[ "$CASE" == delete-failure ]]; then exit 42; fi
        if [[ "$CASE" != still-present ]]; then touch "$REMOVED"; fi ;;
      *) exit 90 ;;
    esac ;;
  kubectl)
    case "$*" in
      *'get nodes '*)
        if [[ "$CASE" == context ]]; then echo unrelated-node
        else printf 'example-control-plane\nexample-worker\n'; fi ;;
      *'get secrets '*)
        if [[ "$CASE" == backup-failure ]]; then exit 43; fi
        printf 'kind: SecretList\nitems: []\n' ;;
      *'get crd '*) if [[ "$CASE" != fresh ]]; then echo crd/applications.argoproj.io; fi ;;
      *'get deployment '*) echo deployment.apps/argocd-applicationset-controller ;;
      *'get applications -o yaml'*) echo 'kind: ApplicationList' ;;
      *'get applications -o name'*) printf 'application.argoproj.io/root\napplication.argoproj.io/staging-myfastapiapp\n' ;;
      *'get applications -o go-template='*) if [[ "$CASE" == sync ]]; then echo staging-myfastapiapp; fi ;;
      *'get jobs '*) if [[ "$CASE" == job ]]; then echo myfastapiapp/myfastapiapp-migrate; fi ;;
      *' wait '*) if [[ "$CASE" == wait-failure ]]; then exit 44; fi ;;
      *' patch '*) if [[ "$CASE" == patch-failure ]]; then exit 45; fi ;;
      *' drain '*) if [[ "$CASE" == drain-failure ]]; then exit 46; fi ;;
      *' scale '*) ;;
      *) exit 91 ;;
    esac ;;
esac
FAKE
chmod +x "$tmp/bin/fake"
for tool in docker kind kubectl; do ln -s fake "$tmp/bin/$tool"; done
fail() { cat "$tmp/out" >&2; cat "$tmp/log" >&2; printf 'FAIL: %s\n' "$*" >&2; exit 1; }
run() {
  scenario="$1"
  input="$2"
  shift 2
  rm -f "$tmp/removed"
  : > "$tmp/log"
  result=0
  printf '%s\n' "$input" | PATH="$tmp/bin:$PATH" CASE="$scenario" LOG="$tmp/log" \
    REMOVED="$tmp/removed" HOME="$tmp/private-home/$scenario" \
    INFRA_LOCAL_KUBECONFIG='/tmp/explicit kubeconfig' KUBECONFIG=/ambient \
    bash "$tmp/workspace/infra-local/scripts/teardown-kind.sh" "$@" > "$tmp/out" 2>&1 || result=$?
}
assert_no_delete() {
  if grep -q '^kind <delete>' "$tmp/log"; then fail 'must not delete cluster'; fi
}
assert_read_only() {
  assert_no_delete
  if grep -Eq '<(patch|scale|drain|wait)>' "$tmp/log"; then fail 'must not mutate cluster'; fi
  [[ ! -d "$tmp/private-home/$scenario" ]] || fail 'must not write backups before confirmation'
}
for scenario in fresh installed; do
  run "$scenario" '' example --dry-run
  [[ "$result" == 0 ]] || fail preview
  assert_read_only
done
for input in '' yes 'delete other-cluster'; do
  run cancel "$input" example
  [[ "$result" != 0 ]] || fail confirmation
  assert_read_only
done
run absent '' example
[[ "$result" == 0 ]] || fail absent
assert_read_only
if grep -q '^kubectl ' "$tmp/log"; then fail 'absent cluster must not use kubeconfig'; fi
for scenario in context docker-failure; do
  run "$scenario" 'delete example' example
  [[ "$result" != 0 ]] || fail "$scenario"
  assert_read_only
done
run invalid 'delete example' --cluster example
[[ "$result" != 0 && ! -s "$tmp/log" ]] || fail 'reject unsupported options before tools'
for scenario in backup-failure wait-failure patch-failure sync job drain-failure; do
  run "$scenario" 'delete example' example
  [[ "$result" != 0 ]] || fail "$scenario must fail"
  assert_no_delete
done
for scenario in fresh installed; do
  run "$scenario" 'delete example' example
  [[ "$result" == 0 && -f "$tmp/removed" ]] || fail "$scenario deletion"
  awk '/<get> <secrets>/ {backup=1} /<drain>/ {if (!backup) exit 1; drained=1} /^kind <delete>/ {if (!drained) exit 1; deleted=1} END {if (!deleted) exit 1}' "$tmp/log" || fail ordering
  grep -q '<drain> <example-control-plane> <example-worker> <--ignore-daemonsets> <--delete-emptydir-data> <--timeout=300s>' "$tmp/log" || fail 'drain all nodes together without force'
  while IFS= read -r command; do
    case "$command" in
      kubectl*) [[ "$command" == *'<--context=kind-example>'* && "$command" == *'<--kubeconfig=/tmp/explicit kubeconfig>'* ]] || fail 'explicit context/file' ;;
      kind*'<delete>'*) [[ "$command" == 'kind <delete> <cluster> <--name> <example>' ]] || fail 'only selected kind cluster' ;;
    esac
  done < "$tmp/log"
  [[ "$(cat "$tmp/workspace/infra-local/terraform/platform/terraform.tfstate")" == 'retained state' ]] || fail 'original state retained'
  copies="$(find "$tmp/private-home/$scenario" -name terraform.tfstate -perm 0600)"
  [[ -n "$copies" ]] || fail 'private state copy'
  [[ -n "$(find "$tmp/private-home/$scenario" -name secrets.yaml -perm 0600)" ]] || fail 'private Secrets copy'
done
for scenario in delete-failure still-present; do
  run "$scenario" 'delete example' example
  [[ "$result" != 0 ]] || fail 'report failed deletion'
  if grep -q '^Cluster example removed\.' "$tmp/out"; then fail 'must not claim success'; fi
done
run unsupported-option 'delete example' example --delete-database
[[ "$result" != 0 && ! -s "$tmp/log" ]] || fail 'reject unsupported database deletion before tools'
printf 'Local teardown safety tests passed.\n'
