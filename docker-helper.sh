#!/bin/bash
# Docker Helper Script for AEMS FastAPI
# Provides convenient commands for common Docker operations

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="aems-fastapi:latest"
CONTAINER_NAME="aems-server"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_usage() {
    cat << EOF
AEMS FastAPI Docker Helper Script

Usage: $0 <command> [options]

Commands:
    build           Build the Docker image
    run             Run the AEMS server container
    stop            Stop the running container
    restart         Restart the container
    logs            Show container logs (follow mode)
    shell           Open a bash shell in the container
    test            Run tests in a container
    health          Check container health status
    clean           Remove container and volumes
    clean-all       Remove everything (container, volumes, images)
    compose-up      Start services with docker-compose
    compose-down    Stop services with docker-compose
    compose-logs    View docker-compose logs

Options:
    -p, --port PORT     Port to expose (default: 8000)
    -d, --detach        Run in detached mode
    -v, --volume PATH   Mount volume for config store
    --dev               Mount source code for development

Environment:
    DERHOST_PUBLISH_HOST  Host-side publish address (default: 127.0.0.1)

Examples:
    $0 build
    $0 run --port 9000
    $0 run --dev
    $0 logs
    $0 shell
    $0 test
    $0 compose-up

EOF
}

print_success() {
    echo -e "${GREEN}[x]${NC} $1"
}

print_error() {
    echo -e "${RED}[!]${NC} $1"
}

print_info() {
    echo -e "${YELLOW}[i]${NC} $1"
}

# Build Docker image
build_image() {
    print_info "Building Docker image: $IMAGE_NAME"
    docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"
    print_success "Image built successfully"
}

# Resolve DERHOST_PUBLISH_HOST to a validated host for the -p publish flag.
# Unset or empty means loopback (#36). Any other value must parse as an IP
# address, passed to python3 via the environment rather than interpolated
# into the script text, so an untrusted value cannot inject code; IPv6 is
# bracketed for docker run's host:port:container syntax.
resolve_publish_host() {
    local raw="${DERHOST_PUBLISH_HOST:-}"
    if [ -z "$raw" ]; then
        printf '%s\n' "127.0.0.1"
        return
    fi
    DERHOST_RAW_HOST="$raw" python3 - <<'PYEOF'
import ipaddress
import os
import sys

raw = os.environ["DERHOST_RAW_HOST"]
try:
    parsed = ipaddress.ip_address(raw)
except ValueError:
    print(f"Error: DERHOST_PUBLISH_HOST is not a valid IP address: {raw!r}", file=sys.stderr)
    sys.exit(1)
print(f"[{parsed}]" if parsed.version == 6 else str(parsed))
PYEOF
}

# Run container
run_container() {
    local port=8000
    local detach="-d"
    local volumes=()
    local dev_mode=false

    while [[ $# -gt 0 ]]; do
        case $1 in
            -p|--port)
                port="$2"
                shift 2
                ;;
            --dev)
                dev_mode=true
                shift
                ;;
            -v|--volume)
                volumes+=("-v" "$2")
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    # Check if container already exists
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_error "Container $CONTAINER_NAME already exists. Stop and remove it first."
        exit 1
    fi

    local publish_host
    publish_host="$(resolve_publish_host)" || exit 1

    print_info "Starting AEMS server on port $port, published on $publish_host"

    local docker_cmd=(docker run $detach --name "$CONTAINER_NAME" -p "${publish_host}:${port}:8000")

    # Add volume for VOLTTRON_HOME
    docker_cmd+=(-v "aems-volttron-home:/var/volttron")

    # Add additional volumes
    for vol in "${volumes[@]}"; do
        docker_cmd+=("$vol")
    done

    # Development mode: mount source code
    if [ "$dev_mode" = true ]; then
        print_info "Running in development mode (source code mounted)"
        docker_cmd+=(-v "${SCRIPT_DIR}/src:/app/src:ro")
    fi

    docker_cmd+=("$IMAGE_NAME")

    "${docker_cmd[@]}"

    print_success "Container started: $CONTAINER_NAME"
    print_info "API docs available at: http://localhost:${port}/docs"
    print_info "Health check at: http://localhost:${port}/health/"
}

# Stop container
stop_container() {
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_info "Stopping container: $CONTAINER_NAME"
        docker stop "$CONTAINER_NAME"
        print_success "Container stopped"
    else
        print_error "Container $CONTAINER_NAME is not running"
    fi
}

# Restart container
restart_container() {
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_info "Restarting container: $CONTAINER_NAME"
        docker restart "$CONTAINER_NAME"
        print_success "Container restarted"
    else
        print_error "Container $CONTAINER_NAME does not exist"
    fi
}

# Show logs
show_logs() {
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_info "Showing logs for: $CONTAINER_NAME (Ctrl+C to exit)"
        docker logs -f "$CONTAINER_NAME"
    else
        print_error "Container $CONTAINER_NAME does not exist"
    fi
}

# Open shell
open_shell() {
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_info "Opening shell in: $CONTAINER_NAME"
        docker exec -it "$CONTAINER_NAME" bash
    else
        print_error "Container $CONTAINER_NAME is not running"
        print_info "Starting a new temporary container with shell..."
        docker run --rm -it "$IMAGE_NAME" bash
    fi
}

# Run tests
run_tests() {
    print_info "Running tests in container"
    docker run --rm "$IMAGE_NAME" pytest tests/ -v
    print_success "Tests completed"
}

# Check health
check_health() {
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        local health_status=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "no healthcheck")
        print_info "Container: $CONTAINER_NAME"
        print_info "Health Status: $health_status"

        if [ "$health_status" = "healthy" ]; then
            print_success "Container is healthy"
        elif [ "$health_status" = "unhealthy" ]; then
            print_error "Container is unhealthy"
            docker inspect --format='{{json .State.Health}}' "$CONTAINER_NAME" | python3 -m json.tool
        else
            print_info "No health check or starting up"
        fi
    else
        print_error "Container $CONTAINER_NAME is not running"
    fi
}

# Clean up
clean_container() {
    print_info "Cleaning up container and volumes"

    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        docker rm -f "$CONTAINER_NAME"
        print_success "Container removed"
    fi

    read -p "Remove VOLTTRON_HOME volume? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker volume rm aems-volttron-home 2>/dev/null && print_success "Volume removed" || print_info "Volume does not exist"
    fi
}

# Clean everything
clean_all() {
    print_info "Removing all AEMS Docker resources"

    # Remove container
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        docker rm -f "$CONTAINER_NAME"
        print_success "Container removed"
    fi

    # Remove volumes
    docker volume rm aems-volttron-home 2>/dev/null && print_success "Volume removed" || print_info "Volume does not exist"

    # Remove image
    docker rmi "$IMAGE_NAME" 2>/dev/null && print_success "Image removed" || print_info "Image does not exist"

    print_success "Cleanup complete"
}

# Docker Compose commands
compose_up() {
    print_info "Starting services with docker-compose"
    docker-compose -f "$SCRIPT_DIR/docker-compose.yml" up -d
    print_success "Services started"
}

compose_down() {
    print_info "Stopping services with docker-compose"
    docker-compose -f "$SCRIPT_DIR/docker-compose.yml" down
    print_success "Services stopped"
}

compose_logs() {
    print_info "Showing docker-compose logs (Ctrl+C to exit)"
    docker-compose -f "$SCRIPT_DIR/docker-compose.yml" logs -f
}

# Main command dispatcher
main() {
    if [ $# -eq 0 ]; then
        print_usage
        exit 1
    fi

    case "$1" in
        build)
            build_image
            ;;
        run)
            shift
            run_container "$@"
            ;;
        stop)
            stop_container
            ;;
        restart)
            restart_container
            ;;
        logs)
            show_logs
            ;;
        shell)
            open_shell
            ;;
        test)
            run_tests
            ;;
        health)
            check_health
            ;;
        clean)
            clean_container
            ;;
        clean-all)
            clean_all
            ;;
        compose-up)
            compose_up
            ;;
        compose-down)
            compose_down
            ;;
        compose-logs)
            compose_logs
            ;;
        help|--help|-h)
            print_usage
            ;;
        *)
            print_error "Unknown command: $1"
            print_usage
            exit 1
            ;;
    esac
}

main "$@"
