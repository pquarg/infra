#!/usr/bin/env bash
# Source after parsing arguments. Fixed roots keep target selection independent of cwd.
select_target() {
  case "$environment" in
    local) target_root="$repository/environments/local" ;;
    aws/dev) target_root="$repository/environments/aws/dev" ;;
    *) printf 'Error: select --environment local|aws/dev.\n' >&2; return 1 ;;
  esac
}
