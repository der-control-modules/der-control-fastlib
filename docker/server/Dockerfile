# derhost server container (#68).
#
# Two stages: `base` holds the Python runtime and the installed `derhost`
# package, reused by future per-agent images (docker/<agent>/Dockerfile
# builds FROM derhost-base:local); `server` adds the FastAPI message bus
# entrypoint. Neither stage adds a docker group or mounts the socket (#50).

FROM debian:bookworm-slim AS base

# setuptools-scm reads git history for the version; a build context taken
# from a git worktree has no usable .git (the worktree's .git file names a
# path outside the image), so the version is pinned by build arg instead.
ARG DERHOST_VERSION=0.0.0+docker

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    VOLTTRON_HOME=/var/volttron \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /usr/sbin/nologin derhost \
    && mkdir -p /var/volttron /app \
    && chown -R derhost:derhost /var/volttron /home/derhost

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY start-legacy.py ./

# Install as root and leave /app root-owned: the runtime user (derhost) must
# not be able to rewrite its own installed code or virtualenv.
RUN python3 -m venv /app/.venv \
    && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DER_CONTROL_FASTLIB="${DERHOST_VERSION}" \
       /app/.venv/bin/pip install --upgrade pip setuptools wheel \
    && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DER_CONTROL_FASTLIB="${DERHOST_VERSION}" \
       /app/.venv/bin/pip install . \
    && chown -R root:root /app \
    && chmod -R a+rX /app

USER derhost

FROM base AS server

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["aems-server", "--host", "0.0.0.0", "--port", "8000"]
