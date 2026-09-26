"""Layout rules for every docker/*/docker-compose.yml (#68).

Each rule is a pure check function asserted against every real compose file
found under docker/, plus a fixture test that proves the same check rejects
a violating service or document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = REPO_ROOT / "docker"

HEALTH_PATH = "/health"


def discover_compose_files() -> list[Path]:
    """Every docker/<name>/docker-compose.yml under the repo root."""
    return sorted(DOCKER_DIR.glob("*/docker-compose.yml"))


def load_compose(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _env_mapping(service: dict[str, Any]) -> dict[str, str]:
    """Normalize compose's two environment forms (mapping, or a KEY=VALUE list)."""
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    pairs: dict[str, str] = {}
    for entry in raw:
        key, _, value = str(entry).partition("=")
        pairs[key] = value
    return pairs


# --- Rule 1: only the server publishes a port. -----------------------------

SERVER_SERVICE = "derhost-server"
SERVER_PORT_MAPPING = "${DERHOST_PUBLISH_HOST:-127.0.0.1}:5410:8000"


def check_only_server_publishes_port(name: str, service: dict[str, Any]) -> None:
    if name == SERVER_SERVICE:
        ports = service.get("ports")
        if ports != [SERVER_PORT_MAPPING]:
            raise AssertionError(f"{name}: expected ports == [{SERVER_PORT_MAPPING}!r], got {ports!r}")
    elif "ports" in service:
        raise AssertionError(f"{name}: publishes a port; only {SERVER_SERVICE} may")


def test_only_server_publishes_port() -> None:
    for path in discover_compose_files():
        compose = load_compose(path)
        for name, service in compose["services"].items():
            check_only_server_publishes_port(name, service)


def test_only_server_publishes_port_fixture_fails() -> None:
    # Control: an agent service that publishes a port must be rejected.
    with pytest.raises(AssertionError, match="publishes a port"):
        check_only_server_publishes_port("derhost-some-agent", {"ports": ["127.0.0.1:9999:9999"]})


def test_server_internal_bind_is_all_interfaces() -> None:
    # The compose port mapping publishes 5410 -> 8000; the container itself
    # must listen on 0.0.0.0:8000 inside the network for that to reach it.
    dockerfile = (DOCKER_DIR / "server" / "Dockerfile").read_text(encoding="utf-8")
    assert '"--host", "0.0.0.0", "--port", "8000"' in dockerfile, dockerfile


# --- Rule 2: no Docker socket mount. ----------------------------------------


def check_no_docker_socket(name: str, service: dict[str, Any]) -> None:
    for volume in service.get("volumes", []):
        if "docker.sock" in str(volume):
            raise AssertionError(f"{name}: mounts the Docker socket: {volume!r}")


def test_no_docker_socket_mount() -> None:
    for path in discover_compose_files():
        compose = load_compose(path)
        for name, service in compose["services"].items():
            check_no_docker_socket(name, service)


def test_no_docker_socket_mount_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="Docker socket"):
        check_no_docker_socket(SERVER_SERVICE, {"volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"]})


# --- Rule 3: non-root user 1000. --------------------------------------------


def check_user_is_1000(name: str, service: dict[str, Any]) -> None:
    if str(service.get("user")) != "1000":
        raise AssertionError(f"{name}: expected user: \"1000\", got {service.get('user')!r}")


def test_server_user_is_1000() -> None:
    compose = load_compose(DOCKER_DIR / "server" / "docker-compose.yml")
    check_user_is_1000(SERVER_SERVICE, compose["services"][SERVER_SERVICE])


def test_user_is_1000_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="user"):
        check_user_is_1000(SERVER_SERVICE, {"user": "0"})


# --- Rule 4: cap_drop ALL. ---------------------------------------------------


def check_cap_drop_all(name: str, service: dict[str, Any]) -> None:
    if "ALL" not in (service.get("cap_drop") or []):
        raise AssertionError(f"{name}: expected cap_drop to include ALL, got {service.get('cap_drop')!r}")


def test_server_cap_drop_all() -> None:
    compose = load_compose(DOCKER_DIR / "server" / "docker-compose.yml")
    check_cap_drop_all(SERVER_SERVICE, compose["services"][SERVER_SERVICE])


def test_cap_drop_all_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="cap_drop"):
        check_cap_drop_all(SERVER_SERVICE, {"cap_drop": ["NET_ADMIN"]})


# --- Rule 5: no-new-privileges. ----------------------------------------------


def check_no_new_privileges(name: str, service: dict[str, Any]) -> None:
    if "no-new-privileges:true" not in (service.get("security_opt") or []):
        raise AssertionError(f"{name}: expected security_opt to include no-new-privileges:true")


def test_server_no_new_privileges() -> None:
    compose = load_compose(DOCKER_DIR / "server" / "docker-compose.yml")
    check_no_new_privileges(SERVER_SERVICE, compose["services"][SERVER_SERVICE])


def test_no_new_privileges_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="no-new-privileges"):
        check_no_new_privileges(SERVER_SERVICE, {"security_opt": []})


# --- Rule 6: restart unless-stopped. -----------------------------------------


def check_restart_unless_stopped(name: str, service: dict[str, Any]) -> None:
    if service.get("restart") != "unless-stopped":
        raise AssertionError(f"{name}: expected restart: unless-stopped, got {service.get('restart')!r}")


def test_server_restart_unless_stopped() -> None:
    compose = load_compose(DOCKER_DIR / "server" / "docker-compose.yml")
    check_restart_unless_stopped(SERVER_SERVICE, compose["services"][SERVER_SERVICE])


def test_restart_unless_stopped_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="restart"):
        check_restart_unless_stopped(SERVER_SERVICE, {"restart": "always"})


# --- Rule 7: /health healthcheck (not the root stack's /health/). -----------


def check_health_endpoint(name: str, service: dict[str, Any]) -> None:
    test = service.get("healthcheck", {}).get("test", [])
    joined = " ".join(str(t) for t in test)
    if HEALTH_PATH not in joined:
        raise AssertionError(f"{name}: healthcheck does not target {HEALTH_PATH}: {test!r}")
    if HEALTH_PATH + "/" in joined:
        raise AssertionError(f"{name}: healthcheck targets the root stack's /health/, not {HEALTH_PATH}")


def test_server_health_endpoint() -> None:
    compose = load_compose(DOCKER_DIR / "server" / "docker-compose.yml")
    check_health_endpoint(SERVER_SERVICE, compose["services"][SERVER_SERVICE])


def test_health_endpoint_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="/health"):
        check_health_endpoint(SERVER_SERVICE, {"healthcheck": {"test": ["CMD", "curl", "-f", "http://localhost:8000/status"]}})


def test_health_endpoint_fixture_rejects_trailing_slash() -> None:
    # Control: the root stack's own healthcheck (/health/) must not pass here.
    with pytest.raises(AssertionError, match="root stack"):
        check_health_endpoint(SERVER_SERVICE, {"healthcheck": {"test": ["CMD", "curl", "-f", "http://localhost:8000/health/"]}})


# --- Rule 8: compose name derhost-<dir>. -------------------------------------


def check_compose_name(path: Path, compose: dict[str, Any]) -> None:
    expected = f"derhost-{path.parent.name}"
    if compose.get("name") != expected:
        raise AssertionError(f"{path}: expected name: {expected}, got {compose.get('name')!r}")


def test_compose_name_matches_directory() -> None:
    for path in discover_compose_files():
        check_compose_name(path, load_compose(path))


def test_compose_name_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="expected name"):
        check_compose_name(DOCKER_DIR / "server" / "docker-compose.yml", {"name": "wrong-name"})


# --- Rule 9: service and container names are derhost-<dir>. -----------------


def check_service_and_container_name(path: Path, compose: dict[str, Any]) -> None:
    expected = f"derhost-{path.parent.name}"
    services = compose["services"]
    if expected not in services:
        raise AssertionError(f"{path}: expected a service named {expected}, got {list(services)!r}")
    if services[expected].get("container_name") != expected:
        raise AssertionError(f"{path}: expected container_name: {expected}, got {services[expected].get('container_name')!r}")


def test_service_and_container_name() -> None:
    for path in discover_compose_files():
        check_service_and_container_name(path, load_compose(path))


def test_service_and_container_name_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="container_name"):
        check_service_and_container_name(
            DOCKER_DIR / "server" / "docker-compose.yml",
            {"services": {SERVER_SERVICE: {"container_name": "something-else"}}},
        )


# --- Rule 10: derhost-net is owned here, not declared external. -------------


def check_network_owned(compose: dict[str, Any]) -> None:
    networks = compose.get("networks") or {}
    if "derhost-net" not in networks:
        raise AssertionError("expected a derhost-net network definition")
    if (networks["derhost-net"] or {}).get("external"):
        raise AssertionError("derhost-net must be owned (not external) in the server compose file")


def test_server_owns_network() -> None:
    compose = load_compose(DOCKER_DIR / "server" / "docker-compose.yml")
    check_network_owned(compose)


def test_network_owned_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="external"):
        check_network_owned({"networks": {"derhost-net": {"external": True}}})


# --- Rule 11: no secret in an environment value. -----------------------------


def check_no_secret_env(name: str, service: dict[str, Any]) -> None:
    if "JWT_SECRET_KEY" in _env_mapping(service):
        raise AssertionError(f"{name}: JWT_SECRET_KEY must not be a literal environment value")


def test_no_secret_env() -> None:
    for path in discover_compose_files():
        compose = load_compose(path)
        for name, service in compose["services"].items():
            check_no_secret_env(name, service)


def test_no_secret_env_fixture_fails() -> None:
    with pytest.raises(AssertionError, match="JWT_SECRET_KEY"):
        check_no_secret_env(SERVER_SERVICE, {"environment": {"JWT_SECRET_KEY": "not-a-real-secret"}})


def test_no_secret_env_fixture_fails_list_form() -> None:
    # Control: the list form (- KEY=VALUE) must be caught too, not just dict form.
    with pytest.raises(AssertionError, match="JWT_SECRET_KEY"):
        check_no_secret_env(SERVER_SERVICE, {"environment": ["JWT_SECRET_KEY=not-a-real-secret"]})
