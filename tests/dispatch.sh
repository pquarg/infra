#!/usr/bin/env bash
set -euo pipefail
repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for script in "$repository"/scripts/*.sh; do bash -n "$script"; done
python3 -m unittest discover -s "$repository/tests" -p 'test_*.py'
