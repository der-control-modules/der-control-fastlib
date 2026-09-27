#!/usr/bin/env bash
# Smoke-tests the docker/server stack end to end (#68): config, build, up,
# health, check, down. Each step is bounded with `timeout` so a hang fails
# the run instead of blocking it. The author runs this locally and pastes
# its output into the PR body, since CI does not build or run containers
# here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/server/docker-compose.yml"
PROJECT_DIR="$SCRIPT_DIR/server"
STEP_TIMEOUT="${DERHOST_SMOKE_STEP_TIMEOUT:-120}"
CLEANED_UP=0

log() {
    printf '[smoke] %s\n' "$1"
}

# An array, not a function: `timeout` execs its argument directly and
# cannot see a shell function, so each step below expands this array in
# place of a `compose` wrapper.
COMPOSE_ARGS=(docker compose -f "$COMPOSE_FILE" --project-directory "$PROJECT_DIR")

step_down() {
    log "down"
    # -v and --rmi local remove the volume and the locally built image, not
    # just the container and network `stack-down` (compose's own default
    # `down`) leaves behind; smoke.sh only ever touches this one compose
    # project, so it tears down by name rather than routing through
    # `stack-down`'s multi-project loop.
    timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" down -v --rmi local
}

refuse_if_stack_exists() {
    # The container name is fixed regardless of DERHOST_PUBLISH_HOST, so a
    # stack already up on a different published address still collides here
    # even though stack-preflight's port-bind check (scoped to one address)
    # would not have caught it: `up -d --build` would recreate someone
    # else's container and this script's teardown would then remove it.
    if docker inspect derhost-server >/dev/null 2>&1; then
        log "refusing: a derhost-server container already exists (not started by this run)"
        exit 1
    fi
}

cleanup() {
    if [ "$CLEANED_UP" -eq 0 ]; then
        CLEANED_UP=1
        # Best-effort: this runs from the EXIT trap, including after a
        # failed step, so a teardown failure here must not mask the
        # original exit status or abort the trap handler itself.
        step_down || true
    fi
}

main() {
    log "config -q"
    timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" config -q

    log "preflight"
    timeout "$STEP_TIMEOUT" make -C "$REPO_ROOT" --no-print-directory stack-preflight

    refuse_if_stack_exists

    log "build"
    timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" build

    log "up"
    # Armed before `up` is invoked, not after it returns: a stack this run
    # never created must never be torn down (config, preflight, build and
    # the existence check above all fail before this line), but a partial
    # failure inside `up` itself (a network or volume created before the
    # container fails to start) must still be cleaned up.
    trap cleanup EXIT INT TERM
    timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" up -d

    log "health"
    timeout "$STEP_TIMEOUT" make -C "$REPO_ROOT" --no-print-directory _stack-wait-healthy

    log "check"
    timeout "$STEP_TIMEOUT" make -C "$REPO_ROOT" --no-print-directory stack-check

    cleanup

    log "smoke passed"
}

main "$@"
