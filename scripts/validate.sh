#!/usr/bin/env bash
set -euo pipefail
repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repository/scripts/targets.sh"
environment=""
all=false
while (( $# )); do
  case "$1" in
    --environment)
      [[ $# -ge 2 && -n "$2" && "$2" != --* && -z "$environment" ]] || exit 1
      environment="$2"; shift 2 ;;
    --all) [[ "$all" == false ]] || exit 1; all=true; shift ;;
    --help|-h) printf 'Usage: %s --environment local|aws/dev | --all\n' "${0##*/}"; exit 0 ;;
    *) printf 'Error: unknown option %s.\n' "$1" >&2; exit 1 ;;
  esac
done
if [[ "$all" == true ]]; then
  [[ -z "$environment" ]] || { printf 'Error: select --all OR --environment.\n' >&2; exit 1; }
else
  select_target
fi
bash "$repository/tests/dispatch.sh"
if [[ "$all" == true ]]; then
  # Run both even if one fails, so every consumer of shared configuration is checked.
  result=0
  "$repository/environments/local/scripts/validate.sh" || result=1
  "$repository/environments/aws/dev/scripts/validate.sh" || result=1
  exit "$result"
fi
exec "$target_root/scripts/validate.sh"
