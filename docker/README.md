# docker/

One container per agent and one for the derhost server (#68), separate from
the root `Dockerfile` / `docker-compose.yml` / `docker-helper.sh`, which stay
as they are and run a different set of services (see the root `docker-*` and
`compose-*` make targets).

Layout: `docker/<name>/` holds that service's `Dockerfile`, `docker-compose.yml`
and `.env.example`; an agent directory also holds `requirements.txt`,
`config.example.json`, `build.sh` and `check-clean.sh`. `docker/lib/` holds
the archive and dirty-checkout logic shared by every agent's `build.sh` and
`check-clean.sh` wrapper. `docker/server/` and `docker/interoperability-service/`
land in earlier PRs; `docker/realtime-control-agent/` runs native control
modes only, with no Julia in the image (operator decision, #68): its baked-in
`config.example.json` sets no mode's `use_julia` to true.

If a configured mode ever sets `use_julia: true` with no `julia` package
installed, the agent does not crash or refuse to start: the import happens
lazily, on the first control tick that mode runs. `derhost`'s scheduler runs
each periodic tick in its own greenlet (`gevent.spawn`, uncaught by the
scheduler's own error handling), so the `ModuleNotFoundError` is logged by
gevent's default handler and the tick is lost, but the process keeps running
and reschedules the next tick regardless.

## Commands

Run from the repository root.

- `make stack-up C=server` - build and start the server.
- `make stack-up C=all` - start the server, then every directory in
  `AGENT_DIRS` (`docker/docker.mk`): interoperability-service and
  realtime-control-agent.
- `make stack-down` - stop everything under `docker/` (each agent first, then
  the server, since agents join the server's network and it must be free of
  attached containers before the server's own `down` can remove it).
- `make stack-status` - `docker compose ps` for each stack.
- `make stack-check` - `GET /connections` from the host and confirm the
  identities in `EXPECTED` (comma-separated) are connected; `EXPECTED=`
  (the default) trivially passes.

`stack-up` refuses before touching Docker when `DERHOST_PUBLISH_HOST` is not
a usable value, or when host port 5410 is already bound.

## Only one stack on port 5410 at a time

The root `docker-compose.yml` stack and `docker/server/docker-compose.yml`
both publish host port 5410, under different container and network names
(`aems-fastlib-server` / `aems-network` vs `derhost-server` / `derhost-net`).
Only one of the two stacks can be up at once. `stack-up` refuses with a
named error, rather than a Docker port-conflict error, when the port is
already taken by the other stack (or anything else).

## Publishing on a different loopback address

Every compose file under `docker/` defaults the published port to
`127.0.0.1`. If that address does not answer on a given host, copy
`docker/server/.env.example` to `docker/server/.env` (gitignored) and set:

```
DERHOST_PUBLISH_HOST=127.0.0.2
```

still loopback-only, just a different address. `stack-up` and `stack-check`
both read this file when `DERHOST_PUBLISH_HOST` is not already set in the
environment; an environment value always wins over the `.env` file.

## Smoke test

`docker/smoke.sh` runs `config -q`, `build`, `up --wait`, `stack-check` and
`down` against `docker/server`, each step bounded with `timeout`, and always
brings the stack down on exit. It runs in its own compose project
(`derhost-smoke`, `docker/server/compose.smoke.yml`), disjoint by name from
the `stack-up` project, so it may run while a developer's stack is up; it
refuses only when a leftover `derhost-smoke` object already exists (a killed
or concurrent run). Its published port is ephemeral, so port 5410 does not
matter to it; `DERHOST_PUBLISH_HOST` still selects the loopback address. CI
does not build or run containers here, so the author runs this locally and
pastes its output into the PR:

```
DERHOST_PUBLISH_HOST=127.0.0.2 docker/smoke.sh
```
