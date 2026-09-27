# interoperability-service agent container (#68).
#
# Builds on derhost-base:local (docker/server/Dockerfile, --target base):
# Python and the installed derhost package, no docker group. Copies only
# the interoperability-service package directory from its own checkout
# (the named build context agent_src, see build.sh), never a config file,
# and never installs the agent's own package or volttron-core: installing
# volttron-core would switch agent.py's import guard to the modern
# volttron.client.* path, which fastlib's compatibility shim does not
# cover.

FROM derhost-base:local

USER root

WORKDIR /agent

COPY requirements.txt ./requirements.txt
RUN /app/.venv/bin/pip install -r requirements.txt \
    && rm requirements.txt \
    && chmod -R a+rX /app/.venv

COPY --from=agent_src --chown=root:root src/interoperability ./interoperability
RUN chmod -R a+rX /agent

ARG AGENT_REVISION=unknown
LABEL org.opencontainers.image.revision=${AGENT_REVISION}

USER derhost

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "import os,sys; sys.exit(0 if any(b'start-legacy.py' in open(f'/proc/{p}/cmdline','rb').read() for p in os.listdir('/proc') if p.isdigit()) else 1)"]

CMD ["python3", "/app/start-legacy.py", "--agent-dir", "/agent", "--module", "interoperability.agent", "--identity", "platform.presentation"]
