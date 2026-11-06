#!/usr/bin/env bash
# Exercise bootstrap ordering and migration guards without a cluster or Python.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT
mkdir -p "$test_root/bin" "$test_root/repo/scripts" "$test_root/repo/terraform/argocd" "$test_root/repo/terraform/platform"
cp "$project_root/scripts/bootstrap-kind.sh" "$project_root/scripts/setup-ingress-kind.sh" "$test_root/repo/scripts/"

cat > "$test_root/bin/fake" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
tool="${0##*/}"
{
  printf '%s' "$tool"
  printf ' <%s>' "$@"
  printf '\n'
} >> "$COMMAND_LOG"
case "$tool" in
  kind) printf 'example-control-plane\n' ;;
  docker) printf '127.0.0.1:80\n' ;;
  terraform)
    if [[ "$*" == *' output '* && "$*" == *'-raw context'* ]]; then
      if [[ "$SCENARIO" == wrong-context && "$*" == *'/platform/'* ]]; then
        printf 'kind-other'
      else
        printf 'kind-example'
      fi
    elif [[ "$*" == *'state list'* ]]; then
      if [[ "$*" == *'/argocd/'* ]]; then
        if [[ "$SCENARIO" == legacy ]]; then
          printf '%s\n' "$LEGACY_ADDRESS"
        else
          printf 'helm_release.argocd\n'
        fi
      else
        printf 'kubernetes_manifest.traefik\n'
      fi
    elif [[ "$*" == *' apply '* && "$SCENARIO" == failed-apply ]]; then
      exit 42
    fi
    ;;
  kubectl)
    if [[ "$*" == *'get deployment argocd-server '* && "$SCENARIO" == untracked-argocd ]]; then
      printf 'deployment.apps/argocd-server\n'
    elif [[ "$*" == *'get application traefik '* && "$SCENARIO" == untracked-traefik ]]; then
      printf 'application.argoproj.io/traefik\n'
    elif [[ "$*" == *'condition=Established'* && "$SCENARIO" == failed-crds ]]; then
      exit 43
    fi
    ;;
esac
STUB
chmod +x "$test_root/bin/fake"
for tool in terraform kubectl kind docker; do
  ln -s fake "$test_root/bin/$tool"
done

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  cat "$test_root/output" >&2
  exit 1
}

run_case() {
  local scenario="$1"
  rm -f "$test_root/repo/terraform/argocd/terraform.tfstate" "$test_root/repo/terraform/platform/terraform.tfstate"
  case "$scenario" in
    tracked|legacy|wrong-context)
      touch "$test_root/repo/terraform/argocd/terraform.tfstate" "$test_root/repo/terraform/platform/terraform.tfstate"
      ;;
  esac
  : > "$test_root/commands"
  result=0
  PATH="$test_root/bin:$PATH" COMMAND_LOG="$test_root/commands" SCENARIO="$scenario" \
    LEGACY_ADDRESS="${legacy_address:-}" \
    INFRA_LOCAL_KUBECONFIG='/tmp/explicit kubeconfig' KUBECONFIG=/tmp/ambient-must-not-be-used \
    bash "$test_root/repo/scripts/bootstrap-kind.sh" example > "$test_root/output" 2>&1 || result=$?
}

apply_count() {
  grep -c '^terraform .*<apply>' "$test_root/commands" || true
}

for scenario in fresh tracked; do
  run_case "$scenario"
  [[ "$result" == 0 && "$(apply_count)" == 2 ]] || fail "$scenario should apply both stages"
  awk '
    /^terraform .*<apply>/ { applies++; if (applies == 2 && !ready) exit 1 }
    /condition=Established/ { if (applies != 1) exit 1; ready=1 }
    END { if (applies != 2 || !ready) exit 1 }
  ' "$test_root/commands" || fail 'registration must follow Argo CD apply and CRD readiness'
  while IFS= read -r command; do
    case "$command" in
      kubectl*)
        [[ "$command" == *'<--context=kind-example>'* && "$command" == *'<--kubeconfig=/tmp/explicit kubeconfig>'* ]] || fail 'kubectl must preserve the explicit context and kubeconfig arguments'
        [[ "$command" != *'<apply>'* ]] || fail 'kubectl must not apply Terraform-owned objects'
        ;;
      terraform*'<apply>'*)
        [[ "$command" == *'<-var=cluster_name=example>'* && "$command" == *'<-var=kubeconfig_path=/tmp/explicit kubeconfig>'* ]] || fail 'Terraform must use the requested cluster and kubeconfig'
        [[ "$command" != *'-auto-approve'* ]] || fail 'applies must require review'
        ;;
    esac
  done < "$test_root/commands"
done

for legacy_address in 'kubernetes_manifest.namespace' \
  'kubernetes_manifest.installation["old-resource"]' \
  'kubectl_manifest.installation["old-secret"]' \
  'kubernetes_secret_v1.installation["old-secret"]'; do
  run_case legacy
  [[ "$result" != 0 && "$(apply_count)" == 0 ]] || fail 'legacy manifest state must stop before applying'
  grep -q 'Helm migration' "$test_root/output" || fail 'legacy state needs an actionable migration message'
  if grep -q '<init>' "$test_root/commands"; then
    fail 'legacy state must be detected before provider initialization'
  fi
done

run_case wrong-context
[[ "$result" != 0 && "$(apply_count)" == 0 ]] || fail 'wrong-cluster state must stop before either apply'

run_case untracked-argocd
[[ "$result" != 0 && "$(apply_count)" == 0 ]] || fail 'an existing untracked Argo CD installation needs adoption first'

run_case untracked-traefik
[[ "$result" != 0 && "$(apply_count)" == 1 ]] || fail 'an existing untracked Traefik Application must not be applied'

run_case failed-apply
[[ "$result" == 42 && "$(apply_count)" == 1 ]] || fail 'a failed Argo CD apply must stop registration'
if grep -q 'condition=Established' "$test_root/commands"; then
  fail 'readiness checks must not run after a failed apply'
fi

run_case failed-crds
[[ "$result" == 43 && "$(apply_count)" == 1 ]] || fail 'failed CRD readiness must stop registration'
printf 'Bootstrap ordering, context and adoption checks passed.\n'
