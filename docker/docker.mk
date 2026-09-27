# Developer commands for the docker/ stack (#68).
#
# Included from the root Makefile. Only docker/server exists at this PR;
# `stack-up C=all` starts the server, then each directory in AGENT_DIRS
# below, so a later per-agent PR adds one word here rather than being
# discovered from the filesystem.
#
# DERHOST_PUBLISH_HOST resolution and the port-5410-busy refusal are
# validated up front (stack-preflight) by asking `docker compose config`
# what it will actually publish on, so preflight agrees with compose by
# construction instead of re-deriving compose's own .env/env precedence.

DOCKER_DIR := docker
SERVER_COMPOSE := $(DOCKER_DIR)/server/docker-compose.yml
SERVER_PROJECT_DIR := $(DOCKER_DIR)/server
SERVER_PROJECT_NAME := derhost-server
DERHOST_STACK_PORT := 5410
DERHOST_HEALTHY_WAIT_SECS := 60

# DERHOST_PUBLISH_HOST, C, EXPECTED and DERHOST_CHECK_BASE_URL are set by the
# caller (environment or `make target VAR=value`) and are not trusted.
#
# They are `unexport`ed so GNU Make never auto-exports their text into a
# recipe's subprocess environment: Make does this for a command-line-set
# variable on every recipe command it runs, whether or not that recipe
# references the variable, and doing so expands a `$(shell ...)` call
# embedded in the value as a side effect of building the environment. A
# Make-level reference ($(NAME)) has the same problem: referencing a
# recursively-flavored variable re-expands its stored text.
#
# So a value never appears as $(NAME) and is never `export`ed here. Where a
# recipe needs it, `$(value NAME)` reads the literal text without
# evaluating anything embedded in it, `shell-safe` single-quotes that text
# for the shell (escaping an embedded `'`), and `pass-through` emits a bare
# `NAME='value'` prefix, or nothing at all when the caller never supplied
# NAME, so an absent value still lets `docker compose` fall through to
# `.env` instead of arriving as an empty override.
#
# Each name's $(origin) is captured here, before `unexport`: `unexport` on
# a name with no prior value defines it (empty) to track that it is not
# exported, which changes its own $(origin) from "undefined" to "file" from
# this point on. Reading $(origin) after that would see every unsupplied
# name as "supplied, empty" and always pass it through.
#
# This block runs first, before anything below that calls $(shell ...):
# unlike $(wildcard), a $(shell ...) call forks a real subprocess, so Make
# must first compute the environment it hands that subprocess, which means
# re-expanding any still-exported command-line variable's text and running
# an embedded `$(shell touch ...)` payload as a side effect, the same
# auto-export mechanism `unexport` exists to stop. Placing the whitespace
# check below ahead of `unexport` would reopen that hole for every
# invocation, whether or not the check itself ever reads these names.
DERHOST_PUBLISH_HOST_ORIGIN := $(origin DERHOST_PUBLISH_HOST)
C_ORIGIN := $(origin C)
EXPECTED_ORIGIN := $(origin EXPECTED)
DERHOST_CHECK_BASE_URL_ORIGIN := $(origin DERHOST_CHECK_BASE_URL)
unexport DERHOST_PUBLISH_HOST C EXPECTED DERHOST_CHECK_BASE_URL
shell-safe = '$(subst ','\'',$(value $(1)))'
pass-through = $(if $(filter-out undefined,$($(1)_ORIGIN)),$(1)=$(call shell-safe,$(1)))

# Agent directories under docker/, named here rather than discovered by a
# filesystem glob: a name this list does not carry is invisible to every
# stack-* target, so filesystem text (a stray directory, a name with a
# shell metacharacter) never reaches a recipe. Empty at this PR; each later
# per-agent PR adds its one directory name. A listed directory with no
# docker-compose.yml fails `stack-up` the same way a typo would.
AGENT_DIRS :=

override OTHER_COMPOSE_FILES := $(foreach d,$(AGENT_DIRS),$(DOCKER_DIR)/$(d)/docker-compose.yml)
export OTHER_COMPOSE_FILES

# Resolves the host address docker compose will actually publish on, by
# asking `docker compose config` (which applies compose's own precedence:
# an explicit env value, even empty, beats `.env`; `.env`'s last matching
# key wins; an unset value falls through to the compose file's own
# default). DERHOST_PUBLISH_HOST reaches this script's environment only
# via the caller's own `pass-through` invocation, never a blanket export.
define DERHOST_RESOLVE_PY
import ipaddress
import json
import os
import subprocess
import sys

# ipaddress.ip_address() accepts a scope id ("fe80::1%eth0") on Python
# 3.9+, which docker compose's ports mapping cannot use; checked here,
# ahead of compose, for a clearer message than compose's own rejection.
raw = os.environ.get("DERHOST_PUBLISH_HOST", "")
if "%" in raw:
    print(f"Error: DERHOST_PUBLISH_HOST carries a scope id, not accepted: {raw!r}", file=sys.stderr)
    sys.exit(1)

compose_file = os.environ["DERHOST_COMPOSE_FILE"]
project_dir = os.environ["DERHOST_PROJECT_DIR"]

try:
    result = subprocess.run(
        ["docker", "compose", "-f", compose_file, "--project-directory", project_dir, "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
except subprocess.CalledProcessError as exc:
    stderr = (exc.stderr or "").strip()
    if "invalid ip address" in stderr.lower():
        print(f"Error: DERHOST_PUBLISH_HOST is not a valid IP address: {stderr}", file=sys.stderr)
    else:
        print(f"Error: docker compose config failed: {stderr}", file=sys.stderr)
    sys.exit(1)
except OSError as exc:
    print(f"Error: could not run docker compose config: {exc}", file=sys.stderr)
    sys.exit(1)

try:
    config = json.loads(result.stdout)
    host = config["services"]["derhost-server"]["ports"][0]["host_ip"]
except (json.JSONDecodeError, KeyError, IndexError) as exc:
    print(f"Error: could not read the published host_ip from docker compose config: {exc}", file=sys.stderr)
    sys.exit(1)

try:
    parsed = ipaddress.ip_address(host)
except ValueError:
    print(f"Error: DERHOST_PUBLISH_HOST is not a valid IP address: {host!r}", file=sys.stderr)
    sys.exit(1)

print(str(parsed))
endef
export DERHOST_RESOLVE_PY

define DERHOST_BIND_CHECK_PY
import errno
import os
import socket
import sys

host = os.environ["DERHOST_RESOLVED_HOST"]
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
            f"Error: {host}:{port} is already bound ({exc}); a derhost or root "
            "stack already publishes that host port, and only one stack runs "
            "at a time",
            file=sys.stderr,
        )
    else:
        print(f"Error: cannot bind {host}:{port}: {exc}", file=sys.stderr)
    sys.exit(1)
finally:
    probe.close()
endef
export DERHOST_BIND_CHECK_PY

define DERHOST_CHECK_PY
import json
import os
import sys
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    # Returns a 3xx response as-is instead of following it, so a
    # forwarded header (the operator token, eventually) never reaches a
    # server this call did not name.
    def http_response(self, request, response):
        return response

    https_response = http_response


base = os.environ["DERHOST_CHECK_BASE_URL"].rstrip("/")
expected = [name for name in os.environ.get("DERHOST_EXPECTED_IDENTITIES", "").split(",") if name.strip()]
opener = urllib.request.build_opener(_NoRedirect)

try:
    with opener.open(f"{base}/connections", timeout=10) as response:
        status = response.status
        if 300 <= status < 400:
            print(f"Error: {base}/connections redirected ({status}), refusing to follow", file=sys.stderr)
            sys.exit(1)
        # _NoRedirect.http_response returns every status as-is, so a 4xx or
        # 5xx never raises on its own; checked here instead, after the
        # redirect case above so a 3xx keeps its own message.
        if not 200 <= status < 300:
            print(f"Error: {base}/connections returned status {status}", file=sys.stderr)
            sys.exit(1)
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

.PHONY: _stack-resolve-host
_stack-resolve-host: ## Print the host DERHOST_PUBLISH_HOST resolves to; binds nothing
	@printf '%s\n' "$$DERHOST_RESOLVE_PY" | $(call pass-through,DERHOST_PUBLISH_HOST) DERHOST_COMPOSE_FILE="$(SERVER_COMPOSE)" DERHOST_PROJECT_DIR="$(SERVER_PROJECT_DIR)" python3 -

.PHONY: stack-preflight
stack-preflight: ## Validate DERHOST_PUBLISH_HOST and refuse if port 5410 is already bound
	@host=$$($(call pass-through,DERHOST_PUBLISH_HOST) $(MAKE) --no-print-directory _stack-resolve-host) || exit 1; \
	printf '%s\n' "$$DERHOST_BIND_CHECK_PY" | DERHOST_RESOLVED_HOST="$$host" DERHOST_RAW_PORT="$(DERHOST_STACK_PORT)" python3 - || exit 1; \
	printf '%s\n' "$$host"

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
	@c=$(call shell-safe,C); \
	if [ "$$c" != "server" ] && [ "$$c" != "all" ]; then \
		echo "Error: set C=server or C=all (got C=$$c)" >&2; \
		exit 1; \
	fi
	$(call pass-through,DERHOST_PUBLISH_HOST) $(MAKE) --no-print-directory stack-preflight
	@$(call pass-through,DERHOST_PUBLISH_HOST) docker compose -p $(SERVER_PROJECT_NAME) -f $(SERVER_COMPOSE) --project-directory $(SERVER_PROJECT_DIR) up -d --build
	$(MAKE) --no-print-directory _stack-wait-healthy
	@c=$(call shell-safe,C); \
	if [ "$$c" = "all" ]; then \
		for f in $$OTHER_COMPOSE_FILES; do \
			dir=$$(dirname "$$f"); \
			docker compose -f "$$f" --project-directory "$$dir" up -d --build; \
		done; \
	fi

.PHONY: stack-down
stack-down: ## Stop the stack (server and any other docker/*/docker-compose.yml)
	-docker compose -p $(SERVER_PROJECT_NAME) -f $(SERVER_COMPOSE) --project-directory $(SERVER_PROJECT_DIR) down
	@for f in $$OTHER_COMPOSE_FILES; do \
		dir=$$(dirname "$$f"); \
		docker compose -f "$$f" --project-directory "$$dir" down || true; \
	done

.PHONY: stack-status
stack-status: ## Show status of the derhost stack
	-docker compose -p $(SERVER_PROJECT_NAME) -f $(SERVER_COMPOSE) --project-directory $(SERVER_PROJECT_DIR) ps
	@for f in $$OTHER_COMPOSE_FILES; do \
		dir=$$(dirname "$$f"); \
		docker compose -f "$$f" --project-directory "$$dir" ps || true; \
	done

.PHONY: stack-check
stack-check: ## GET /connections and confirm EXPECTED (comma-separated) identities are there
	@base=$(call shell-safe,DERHOST_CHECK_BASE_URL); \
	if [ -z "$$base" ]; then \
		host=$$(printf '%s\n' "$$DERHOST_RESOLVE_PY" | $(call pass-through,DERHOST_PUBLISH_HOST) DERHOST_COMPOSE_FILE="$(SERVER_COMPOSE)" DERHOST_PROJECT_DIR="$(SERVER_PROJECT_DIR)" python3 -) || exit 1; \
		case "$$host" in \
			*:*) base="http://[$$host]:$(DERHOST_STACK_PORT)" ;; \
			*) base="http://$$host:$(DERHOST_STACK_PORT)" ;; \
		esac; \
	fi; \
	printf '%s\n' "$$DERHOST_CHECK_PY" | DERHOST_CHECK_BASE_URL="$$base" DERHOST_EXPECTED_IDENTITIES=$(call shell-safe,EXPECTED) python3 -
