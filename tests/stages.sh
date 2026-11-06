#!/usr/bin/env bash
# Stage preflight and planning guards without AWS or Terraform access.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/repo/scripts" "$tmp/repo/config" "$tmp/repo/terraform/foundation" "$tmp/repo/terraform/platform" "$tmp/repo/terraform/edge"
cp "$root/scripts/check-stage.sh" "$root/scripts/plan.sh" "$tmp/repo/scripts/"
touch "$tmp/repo/config/operator.tfvars"
cat > "$tmp/bin/fake" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s' "${0##*/}" >> "$LOG"
printf ' <%s>' "$@" >> "$LOG"
printf '\n' >> "$LOG"
case "${0##*/}" in
  aws)
    case "$*" in
      *get-caller-identity*) if [[ "$CASE" == account ]]; then echo wrong; else echo 245361883993; fi ;;
      *describe-cluster*) echo https://eks.example ;;
      *list-pod-identity-associations*) if [[ "$CASE" != identity ]]; then echo association; fi ;;
      *describe-load-balancers*) if [[ "$CASE" != alb ]]; then echo arn:alb; fi ;;
      *describe-target-groups*) echo arn:target ;;
      *describe-target-health*) if [[ "$CASE" == unhealthy ]]; then echo 0; else echo 1; fi ;;
    esac ;;
  kubectl)
    if [[ "$*" == *'config view'* ]]; then
      if [[ "$CASE" == context ]]; then echo https://wrong; else echo https://eks.example; fi
    elif [[ "$*" == *'rollout status'* && "$CASE" == controller ]]; then exit 42; fi ;;
  terraform)
    if [[ "$*" == *'state list'* && "$CASE" == legacy ]]; then echo helm_release.argocd; fi ;;
esac
FAKE
chmod +x "$tmp/bin/fake"
for tool in aws kubectl terraform; do ln -s fake "$tmp/bin/$tool"; done
fail() { cat "$tmp/out" >&2; printf 'FAIL: %s\n' "$*" >&2; exit 1; }
run() {
  : > "$tmp/log"; result=0
  CASE="$1" LOG="$tmp/log" PATH="$tmp/bin:$PATH" bash "$tmp/repo/scripts/plan.sh" "$2" --context selected --kubeconfig '/tmp/selected file' > "$tmp/out" 2>&1 || result=$?
}
for stage in foundation platform edge; do
  run success "$stage"
  [[ "$result" == 0 ]] || fail "$stage plan"
  grep -q "terraform/$stage> <plan>" "$tmp/log" || fail 'owning stage'
  if grep -q '<apply>\|target=' "$tmp/log"; then fail 'only full plan permitted'; fi
  if [[ "$stage" != platform ]]; then grep -q 'operator.tfvars>' "$tmp/log" || fail 'shared allowlist'; fi
  if [[ "$stage" == foundation ]] && grep -q describe-load-balancers "$tmp/log"; then fail 'foundation must not require ALB'; fi
done
for scenario in account context identity controller alb unhealthy; do
  run "$scenario" edge
  [[ "$result" != 0 ]] || fail "$scenario preflight"
  if grep -q '<plan>' "$tmp/log"; then fail 'no plan on failed prerequisites'; fi
done
for stage in foundation platform edge; do
  run legacy "$stage"
  [[ "$result" != 0 ]] || fail 'legacy state must stop'
  if grep -q '<plan>' "$tmp/log"; then fail 'no plan with legacy owners'; fi
done
printf 'AWS stage checks passed.\n'
