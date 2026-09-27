#!/usr/bin/env bash
# Refuse to build the interoperability-service image from a dirty agent
# checkout, unless ALLOW_DIRTY=1 (#68). Kept separate from build.sh so a
# pytest test can drive it directly against a scratch git checkout.
#
# Usage: check-clean.sh <path-to-checkout>
set -euo pipefail

main() {
  if [ "$#" -ne 1 ]; then
    echo "usage: check-clean.sh <path-to-checkout>" >&2
    exit 2
  fi
  local checkout="$1"

  if [ "${ALLOW_DIRTY:-0}" = "1" ]; then
    echo "check-clean: ALLOW_DIRTY=1, skipping the dirty-checkout check for ${checkout}" >&2
    exit 0
  fi

  if ! git -C "${checkout}" rev-parse --git-dir >/dev/null 2>&1; then
    echo "check-clean: ${checkout} is not a git checkout" >&2
    exit 1
  fi

  local status
  status="$(git -C "${checkout}" status --porcelain)"
  if [ -n "${status}" ]; then
    echo "check-clean: ${checkout} has uncommitted changes; refusing to build from it:" >&2
    echo "${status}" >&2
    echo "check-clean: set ALLOW_DIRTY=1 to build anyway." >&2
    exit 1
  fi

  echo "check-clean: ${checkout} is clean." >&2
}

main "$@"
