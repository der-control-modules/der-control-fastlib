# AEMS FastAPI Docker Container
# Base: Latest Debian (bookworm)

FROM debian:bookworm-slim

# Build arg for Docker socket group GID (match host docker group)
ARG DOCKER_GID=125

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    VOLTTRON_HOME=/var/volttron \
    AEMS_PORT=8000

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    git \
    curl \
    ca-certificates \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Create application directory, volttron home, and directories for git repos
RUN mkdir -p /app /var/volttron/aems_config_store /volttron /volttron-pnnl-aems /volttron-pnnl-applications && \
    useradd -m -u 1000 -s /bin/bash volttron && \
    groupadd -g ${DOCKER_GID} docker-host 2>/dev/null || true && \
    usermod -aG docker-host volttron && \
    chown -R volttron:volttron /app /var/volttron /volttron /volttron-pnnl-aems /volttron-pnnl-applications

# Clone VOLTTRON repository (version 9.0.4 tag) for legacy agent support
RUN git clone --branch 9.0.4 --depth 1 https://github.com/VOLTTRON/volttron.git /volttron

# Clone VOLTTRON PNNL AEMS repository (feature/29-add-endpoint-for-agent branch)
# Primary directory needed: aems-edge (keeping full repo for Docker layer efficiency)
RUN git clone --branch feature/29-add-endpoint-for-agent --depth 1 \
    https://github.com/VOLTTRON/volttron-pnnl-aems.git /volttron-pnnl-aems

# Clone VOLTTRON PNNL Applications repository for ILC agent
RUN git clone --depth 1 \
    https://github.com/VOLTTRON/volttron-pnnl-applications.git /volttron-pnnl-applications

# Fix ownership after git clones (clones run as root, so files are root-owned)
RUN chown -R volttron:volttron /volttron /volttron-pnnl-aems /volttron-pnnl-applications

# Set working directory
WORKDIR /app

# Switch to volttron user
USER volttron

# Copy only requirements first for better caching
COPY --chown=volttron:volttron pyproject.toml ./

# Create virtual environment and install dependencies
RUN python3 -m venv /app/.venv && \
    /app/.venv/bin/pip install --upgrade pip setuptools wheel && \
    /app/.venv/bin/pip install build

# Copy the entire project including .git for setuptools-scm
COPY --chown=volttron:volttron . .
# Note: .git folder is needed for setuptools-scm to detect version

# Install the package with all optional dependencies except dev/testing
# Includes: historians, drivers, ilc, weather, and manager
RUN /app/.venv/bin/pip install -e ".[historians,drivers,ilc,weather,manager]"

# Expose the default AEMS server port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

# Set the PATH to include the virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Default command: start the AEMS server
CMD ["aems-server", "--host", "0.0.0.0", "--port", "8000"]
