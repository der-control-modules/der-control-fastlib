#!/usr/bin/env bash
# Smoke-tests the docker/server stack end to end (#68): config, build, up,
# health wait, check, down, entirely inside its own compose project so a
# run never names or removes a developer's `stack-up` objects. The author
# runs this locally and pastes its output into the PR body, since CI does
# not build or run containers here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/server/docker-compose.yml"
SMOKE_OVERLAY="$SCRIPT_DIR/server/compose.smoke.yml"
PROJECT_DIR="$SCRIPT_DIR/server"
PROJECT_NAME="derhost-smoke"
STEP_TIMEOUT="${DERHOST_SMOKE_STEP_TIMEOUT:-120}"
WAIT_TIMEOUT="${DERHOST_SMOKE_WAIT_TIMEOUT:-60}"
CLEANED_UP=0

log() {
    printf '[smoke] %s\n' "$1"
}

# An array, not a function: `timeout` execs its argument directly and
# cannot see a shell function, so each step below expands this array in
# place of a `compose` wrapper. -p and the overlay pin every object this
# run creates to its own project, disjoint by name from a developer's
# `stack-up` project (docker/server/compose.smoke.yml).
COMPOSE_ARGS=(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" -f "$SMOKE_OVERLAY" --project-directory "$PROJECT_DIR")

step_down() {
    log "down"
    # -v and --rmi local remove the volume and the image this project
    # built, not just the container and network compose's own default
    # `down` leaves behind. Safe unconditionally, not by convention: every
    # name in this project is unique to it, so there is nothing of a
    # developer's stack for this call to reach.
    timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" down -v --rmi local
}

refuse_if_leftover_exists() {
    # A killed or concurrent smoke run leaves objects still carrying this
    # project's label; `build`/`up` would then attach to that older run's
    # objects instead of ones this run created, and this run's `down`
    # would then remove them. Compose labels containers, networks and
    # volumes alike with com.docker.compose.project, so all three are
    # checked.
    local label="label=com.docker.compose.project=$PROJECT_NAME"
    local leftover
    leftover="$(docker ps -aq --filter "$label")$(docker network ls -q --filter "$label")$(docker volume ls -q --filter "$label")"
    if [ -n "$leftover" ]; then
        log "refusing: a $PROJECT_NAME object already exists (not started by this run)"
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

    refuse_if_leftover_exists

    log "build"
    timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" build

    log "up"
    # Armed before `up` is invoked, not after it returns: a stack this run
    # never created must never be torn down (config and the leftover check
    # above both fail before this line), but a partial failure inside `up`
    # itself (a network or volume created before the container fails to
    # start) must still be cleaned up.
    trap cleanup EXIT INT TERM
    timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT"

    log "check"
    local port
    port="$(timeout "$STEP_TIMEOUT" "${COMPOSE_ARGS[@]}" port derhost-server 8000)"
    timeout "$STEP_TIMEOUT" make -C "$REPO_ROOT" --no-print-directory stack-check "DERHOST_CHECK_BASE_URL=http://${port}"

    cleanup

    log "smoke passed"
}

main "$@"
