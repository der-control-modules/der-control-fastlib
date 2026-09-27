#!/usr/bin/env bash
# Shared build logic for a docker/<agent>/build.sh (#68 realtime). Sourced,
# not executed directly: the caller sets SCRIPT_DIR, REPO_ROOT and LIB_DIR
# (its own build.sh's boilerplate, unchanged from before this file existed)
# and then calls build_agent_image with the two things that differ per
# agent: the default sibling checkout name, and the path(s) `git archive`
# exports.

# Global, not `local` to build_agent_image: the EXIT trap this function sets
# runs after the function (and the sourcing script's main) returns, once any
# local variable is out of scope, and needs this to still resolve.
archive_dir=""

build_agent_image() {
  local default_sibling="$1"
  shift

  command -v docker >/dev/null 2>&1 || {
    echo "build: docker is required and not on PATH" >&2
    exit 1
  }

  # check-clean.sh validates agent_src itself, not-a-checkout included;
  # compose never sees agent_src directly, only the absolute archive_dir
  # exported below, so this path does not need canonicalizing (#68).
  local agent_src="${DER_AGENT_SRC:-${REPO_ROOT}/../${default_sibling}}"
  "${LIB_DIR}/check-clean.sh" "${agent_src}"

  export AGENT_REVISION
  AGENT_REVISION="$(git -C "${agent_src}" rev-parse HEAD)"

  archive_dir="$(mktemp -d)"
  trap 'rm -rf "${archive_dir}"' EXIT
  # git archive exports the committed tree only, so a gitignored or
  # otherwise uncommitted file in agent_src (an ALLOW_DIRTY=1 build allows
  # exactly that) never reaches the build context or the image (#68).
  # AGENT_REVISION is always this HEAD sha with no suffix: ALLOW_DIRTY only
  # bypasses check-clean.sh's refusal to build, it never changes what gets
  # archived, so a "-dirty" label would claim content the image never ships.
  git -C "${agent_src}" archive HEAD -- "$@" | tar -x -C "${archive_dir}"
  export DER_AGENT_SRC="${archive_dir}"

  # Shared by every agent image; only build it when missing so one agent's
  # rebuild does not force a rebuild for agents already running.
  if ! docker image inspect derhost-base:local >/dev/null 2>&1; then
    docker build --target base -t derhost-base:local -f "${REPO_ROOT}/docker/server/Dockerfile" "${REPO_ROOT}"
  fi

  docker compose -f "${SCRIPT_DIR}/docker-compose.yml" build
}
