# realtime-control-agent container (#68).
#
# Builds on derhost-base:local (docker/server/Dockerfile, --target base):
# Python and the installed derhost package, no docker group. Copies only
# the rt_control package directory from its own checkout (the named build
# context agent_src, see build.sh), never a config file except the baked-in
# config.example.json below, and never installs the agent's own package,
# volttron, volttron-core or julia: installing volttron-core would switch
# agent.py's import guard to the modern volttron.client.* path, which
# fastlib's compatibility shim does not cover; the operator has decided
# native control modes only, so Julia is never installed in this image
# (issue #68).
#
# --debug on the CMD is load-bearing, not cosmetic: the periodic control
# tick (rt_control/agent.py's loop()) and the scheduler's own per-event
# trace both log at DEBUG only, so without it the container's log never
# shows a tick even while the loop runs correctly every --config
# "resolution" seconds.

FROM derhost-base:local

USER root

WORKDIR /agent

COPY requirements.txt ./requirements.txt
RUN /app/.venv/bin/pip install -r requirements.txt \
    && rm requirements.txt \
    && chmod -R a+rX /app/.venv

COPY --from=agent_src --chown=root:root rt_control ./rt_control
COPY --chown=root:root config.example.json ./config.json
RUN chmod -R a+rX /agent

ARG AGENT_REVISION=unknown
LABEL org.opencontainers.image.revision=${AGENT_REVISION}

USER derhost

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "import os,sys; sys.exit(0 if any(b'start-legacy.py' in open(f'/proc/{p}/cmdline','rb').read() for p in os.listdir('/proc') if p.isdigit()) else 1)"]

CMD ["python3", "/app/start-legacy.py", "--agent-dir", "/agent", "--module", "rt_control.agent", "--config", "config.json", "--identity", "der.rtc", "--debug"]
