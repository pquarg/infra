#!/usr/bin/env bash
# No cluster, state mutation or Python required.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/infra/environments/local/scripts" "$tmp/infra/environments/local/platform"
cp "$root/scripts/bootstrap-kind.sh" "$root/scripts/check-ingress-kind.sh" "$root/scripts/check-platform.sh" "$tmp/infra/environments/local/scripts/"
mkdir -p "$tmp/infra/scripts"
cp "$root/../../scripts/check-gateway.py" "$tmp/infra/scripts/"
export GATEWAY_FIXTURE="$root/../../tests/gateway-status.py"
cat > "$tmp/bin/fake" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s' "${0##*/}" >> "$LOG"
printf ' <%s>' "$@" >> "$LOG"
printf '\n' >> "$LOG"
case "${0##*/}" in
  kind) echo example-control-plane ;;
  docker) if [[ "$CASE" != ports ]]; then echo 127.0.0.1:80; fi ;;
  terraform)
    if [[ "$*" == *' output '* ]]; then
      if [[ "$CASE" == context ]]; then echo kind-other; else echo kind-example; fi
    elif [[ "$*" == *'state list'* ]]; then
      case "$CASE" in
        unexpected) echo "$ADDRESS" ;;
        upgrade) printf 'helm_release.argocd\nhelm_release.traefik\n' ;;
        tracked) printf 'helm_release.argocd\nhelm_release.traefik\nhelm_release.gateway_api_crds\n' ;;
      esac
    elif [[ "$*" == *' apply '* && "$CASE" == apply ]]; then exit 42; fi ;;
  kubectl)
    if [[ "$*" == *'get nodes'* ]]; then echo example-control-plane; fi
    case "$CASE:$*" in
      untracked-argocd:*'get deployment argocd-server '*|untracked-traefik:*'get deployment traefik '*) echo existing ;;
      record:*'get secrets '*) echo secret/release ;;
      crds:*'condition=Established'*) exit 43 ;;
    esac
    if [[ "$*" == *'get gatewayclass '* || "$*" == *'get gateway '* ]]; then python3 "$GATEWAY_FIXTURE" "$@"; fi
    if [[ "$CASE" == untracked-crds && "$*" == *'get crds '* ]]; then echo customresourcedefinition.apiextensions.k8s.io/gateways.gateway.networking.k8s.io; fi
    if [[ "$CASE" == crd-record && "$*" == *'name=gateway-api-crds'* ]]; then echo secret/release; fi
    if [[ "$*" == *'get service traefik '* ]]; then echo 30080; fi ;;
esac
FAKE
chmod +x "$tmp/bin/fake"
for tool in terraform kubectl kind docker; do ln -s fake "$tmp/bin/$tool"; done
fail() { cat "$tmp/out" >&2; printf 'FAIL: %s\n' "$*" >&2; exit 1; }
run() {
  rm -f "$tmp/infra/environments/local/platform/terraform.tfstate"
  case "$1" in
    unexpected|context|tracked|upgrade) touch "$tmp/infra/environments/local/platform/terraform.tfstate" ;;
  esac
  : > "$tmp/log"
  result=0
  PATH="$tmp/bin:$PATH" CASE="$1" ADDRESS="${address:-}" LOG="$tmp/log" \
    INFRA_LOCAL_KUBECONFIG='/tmp/explicit kubeconfig' KUBECONFIG=/ambient \
    bash "$tmp/infra/environments/local/scripts/bootstrap-kind.sh" example > "$tmp/out" 2>&1 || result=$?
}
applies() { grep -c '^terraform .*<apply>' "$tmp/log" || true; }
for scenario in fresh tracked upgrade; do
  run "$scenario"
  [[ "$result" == 0 && "$(applies)" == 1 ]] || fail "$scenario: one apply expected"
  awk '/<apply>/ {applied=1} /condition=Established/ {if (!applied) exit 1; ready=1} END {if (!ready) exit 1}' "$tmp/log" || fail ordering
  while IFS= read -r command; do
    case "$command" in
      kubectl*) [[ "$command" == *'<--context=kind-example>'* && "$command" == *'<--kubeconfig=/tmp/explicit kubeconfig>'* && "$command" != *'<apply>'* ]] || fail context ;;
      terraform*'<apply>'*) [[ "$command" == *'<-var=cluster_name=example>'* && "$command" == *'<-var=kubeconfig_path=/tmp/explicit kubeconfig>'* && "$command" != *auto-approve* ]] || fail review ;;
    esac
  done < "$tmp/log"
done
for address in helm_release.other aws_instance.other; do
  run unexpected
  [[ "$result" != 0 && "$(applies)" == 0 ]] || fail 'unexpected state resource'
  if grep -q '<init>' "$tmp/log"; then fail 'unexpected state must stop before init'; fi
done
for scenario in context untracked-argocd untracked-traefik record untracked-crds crd-record; do
  run "$scenario"
  [[ "$result" != 0 && "$(applies)" == 0 ]] || fail "$scenario must stop before apply"
done
run apply
[[ "$result" == 42 && "$(applies)" == 1 ]] || fail 'apply failure propagation'
if grep -q condition=Established "$tmp/log"; then fail 'no readiness after failure'; fi
for scenario in crds ports class unaccepted unresolved stale; do
  run "$scenario"
  [[ "$result" != 0 && "$(applies)" == 1 ]] || fail "$scenario failure propagation"
done
printf 'Platform bootstrap safety tests passed.\n'
