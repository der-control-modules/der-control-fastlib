#!/usr/bin/env bash
# Build derhost-base:local (if missing) and the interoperability-service
# agent image, stamping the agent checkout's revision into
# org.opencontainers.image.revision (#68). Shared build logic now lives in
# docker/lib/build-agent.sh (#68 realtime); this file supplies only what
# differs per agent.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd)"
LIB_DIR="${REPO_ROOT}/docker/lib"

# shellcheck source=../lib/build-agent.sh
source "${LIB_DIR}/build-agent.sh"

main() {
  build_agent_image "interoperability-service" src/interoperability
}

main "$@"
