#!/usr/bin/env bash
# Build derhost-base:local (if missing) and the interoperability-service
# agent image, stamping the agent checkout's revision into
# org.opencontainers.image.revision (#68).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd)"

main() {
  command -v docker >/dev/null 2>&1 || {
    echo "build: docker is required and not on PATH" >&2
    exit 1
  }

  local agent_src="${DER_AGENT_SRC:-${REPO_ROOT}/../interoperability-service}"
  "${SCRIPT_DIR}/check-clean.sh" "${agent_src}"

  # Shared by every future agent image; only build it when missing so one
  # agent's rebuild does not force a rebuild for agents already running.
  if ! docker image inspect derhost-base:local >/dev/null 2>&1; then
    docker build --target base -t derhost-base:local -f "${REPO_ROOT}/docker/server/Dockerfile" "${REPO_ROOT}"
  fi

  export DER_AGENT_SRC="${agent_src}"
  export AGENT_REVISION
  AGENT_REVISION="$(git -C "${agent_src}" rev-parse HEAD)"

  docker compose -f "${SCRIPT_DIR}/docker-compose.yml" build
}

main "$@"
