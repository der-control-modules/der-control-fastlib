# Developer commands for the docker/ stack (#68).
#
# Included from the root Makefile. Only docker/server exists at this PR;
# `stack-up C=all` starts the server, then any other docker/*/docker-compose.yml
# discovered under docker/, so a later per-agent PR needs no wiring here.
#
# DERHOST_PUBLISH_HOST resolution and the port-5410-busy refusal are
# validated up front (stack-preflight), separately from docker-compose.yml's
# own `${DERHOST_PUBLISH_HOST:-127.0.0.1}` interpolation, because compose
# cannot reject a scope id or refuse to start when the port is already the
# root stack's.

DOCKER_DIR := docker
SERVER_COMPOSE := $(DOCKER_DIR)/server/docker-compose.yml
SERVER_PROJECT_DIR := $(DOCKER_DIR)/server
SERVER_ENV := $(DOCKER_DIR)/server/.env
DERHOST_STACK_PORT := 5410
DERHOST_HEALTHY_WAIT_SECS := 60
OTHER_COMPOSE_FILES := $(filter-out $(SERVER_COMPOSE),$(wildcard $(DOCKER_DIR)/*/docker-compose.yml))

# DERHOST_PUBLISH_HOST, C, EXPECTED and DERHOST_CHECK_BASE_URL are set by the
# caller (environment or `make target VAR=value`) and are not trusted. Every
# recipe below reads them as a real shell environment variable ($$NAME,
# expanded by the shell) rather than a Make variable ($(NAME), expanded by
# Make into the recipe's literal text before the shell ever sees it): a value
# containing a `"` breaks out of a Make-substituted string and runs as shell
# syntax, which a shell-level expansion does not. `export` makes each name
# reach the recipe environment even when it is left at its Make-side default.
export DERHOST_PUBLISH_HOST
export C
export EXPECTED

# The raw value is passed via the environment, never interpolated into the
# script text, so an untrusted value cannot inject code (same convention as
# docker-helper.sh's resolve_publish_host). Unlike that copy, this one also
# rejects a scope id: ipaddress.ip_address() accepts "fe80::1%eth0" on
# Python 3.9+, which is not a value docker compose's ports mapping can use.
define DERHOST_PREFLIGHT_PY
import errno
import ipaddress
import os
import socket
import sys

raw = os.environ.get("DERHOST_ENV_VALUE", "")
if not raw:
    env_file = os.environ.get("DERHOST_ENV_FILE", "")
    if env_file and os.path.isfile(env_file):
        with open(env_file, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                if sep and key.strip() == "DERHOST_PUBLISH_HOST":
                    raw = value.strip().strip('"').strip("'")
                    break

if not raw:
    host = "127.0.0.1"
elif "%" in raw:
    print(f"Error: DERHOST_PUBLISH_HOST carries a scope id, not accepted: {raw!r}", file=sys.stderr)
    sys.exit(1)
else:
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        print(f"Error: DERHOST_PUBLISH_HOST is not a valid IP address: {raw!r}", file=sys.stderr)
        sys.exit(1)
    host = str(parsed)

port = int(os.environ["DERHOST_RAW_PORT"])
family, socktype, proto, _, sockaddr = socket.getaddrinfo(
    host, port, type=socket.SOCK_STREAM
)[0]
probe = socket.socket(family, socktype, proto)
probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    probe.bind(sockaddr)
except OSError as exc:
    if exc.errno == errno.EADDRINUSE:
        print(
            f"Error: {host}:{port} is already bound ({exc}); the root docker-compose.yml "
            "stack publishes the same host port, and only one stack runs at a time",
            file=sys.stderr,
        )
    else:
        print(f"Error: cannot bind {host}:{port}: {exc}", file=sys.stderr)
    sys.exit(1)
finally:
    probe.close()

print(host)
endef
export DERHOST_PREFLIGHT_PY

# DERHOST_CHECK_BASE_URL is overridable so a test can point this at a stub
# HTTP server instead of a running derhost-server.
DERHOST_CHECK_BASE_URL ?= http://$(if $(DERHOST_PUBLISH_HOST),$(DERHOST_PUBLISH_HOST),127.0.0.1):$(DERHOST_STACK_PORT)
export DERHOST_CHECK_BASE_URL

define DERHOST_CHECK_PY
import json
import os
import sys
import urllib.error
import urllib.request

base = os.environ["DERHOST_CHECK_BASE_URL"].rstrip("/")
expected = [name for name in os.environ.get("DERHOST_EXPECTED_IDENTITIES", "").split(",") if name.strip()]

try:
    with urllib.request.urlopen(f"{base}/connections", timeout=10) as response:
        body = json.load(response)
except (urllib.error.URLError, OSError, ValueError) as exc:
    print(f"Error: could not read {base}/connections: {exc}", file=sys.stderr)
    sys.exit(1)

connected = set(body.get("connections", {}))
missing = False
for identity in expected:
    identity = identity.strip()
    if identity in connected:
        print(f"{identity}: connected")
    else:
        print(f"{identity}: MISSING")
        missing = True

sys.exit(1 if missing else 0)
endef
export DERHOST_CHECK_PY

.PHONY: stack-preflight
stack-preflight: ## Validate DERHOST_PUBLISH_HOST and refuse if port 5410 is already bound
	@printf '%s\n' "$$DERHOST_PREFLIGHT_PY" | DERHOST_ENV_VALUE="$${DERHOST_PUBLISH_HOST:-}" DERHOST_ENV_FILE="$(SERVER_ENV)" DERHOST_RAW_PORT="$(DERHOST_STACK_PORT)" python3 -

.PHONY: _stack-wait-healthy
_stack-wait-healthy:
	@elapsed=0; \
	while true; do \
		status=$$(docker inspect --format '{{.State.Health.Status}}' derhost-server 2>/dev/null || echo unknown); \
		if [ "$$status" = "healthy" ]; then \
			echo "derhost-server is healthy"; \
			break; \
		fi; \
		if [ "$$elapsed" -ge $(DERHOST_HEALTHY_WAIT_SECS) ]; then \
			echo "Error: derhost-server did not become healthy within $(DERHOST_HEALTHY_WAIT_SECS)s (last status: $$status)" >&2; \
			exit 1; \
		fi; \
		sleep 1; \
		elapsed=$$((elapsed + 1)); \
	done

.PHONY: stack-up
stack-up: ## Start the stack: C=server or C=all (server first, then any other docker/*)
	@c="$${C:-}"; \
	if [ "$$c" != "server" ] && [ "$$c" != "all" ]; then \
		echo "Error: set C=server or C=all (got C=$$c)" >&2; \
		exit 1; \
	fi
	$(MAKE) --no-print-directory stack-preflight
	docker compose -f $(SERVER_COMPOSE) --project-directory $(SERVER_PROJECT_DIR) up -d --build
	$(MAKE) --no-print-directory _stack-wait-healthy
	@if [ "$${C:-}" = "all" ]; then \
		for f in $(OTHER_COMPOSE_FILES); do \
			dir=$$(dirname "$$f"); \
			docker compose -f "$$f" --project-directory "$$dir" up -d --build; \
		done; \
	fi

.PHONY: stack-down
stack-down: ## Stop the stack (server and any other docker/*/docker-compose.yml)
	-docker compose -f $(SERVER_COMPOSE) --project-directory $(SERVER_PROJECT_DIR) down
	@for f in $(OTHER_COMPOSE_FILES); do \
		dir=$$(dirname "$$f"); \
		docker compose -f "$$f" --project-directory "$$dir" down; \
	done

.PHONY: stack-status
stack-status: ## Show status of the derhost stack
	-docker compose -f $(SERVER_COMPOSE) --project-directory $(SERVER_PROJECT_DIR) ps
	@for f in $(OTHER_COMPOSE_FILES); do \
		dir=$$(dirname "$$f"); \
		docker compose -f "$$f" --project-directory "$$dir" ps; \
	done

.PHONY: stack-check
stack-check: ## GET /connections and confirm EXPECTED (comma-separated) identities are there
	@printf '%s\n' "$$DERHOST_CHECK_PY" | DERHOST_CHECK_BASE_URL="$$DERHOST_CHECK_BASE_URL" DERHOST_EXPECTED_IDENTITIES="$${EXPECTED:-}" python3 -
