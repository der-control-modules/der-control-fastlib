#!/usr/bin/env bash
# Thin wrapper: the dirty-checkout check itself lives in
# docker/lib/check-clean.sh, shared with every other agent (#68).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
exec "${SCRIPT_DIR}/../lib/check-clean.sh" "$@"
