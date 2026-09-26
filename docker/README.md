# docker/

One container per agent and one for the derhost server (#68), separate from
the root `Dockerfile` / `docker-compose.yml` / `docker-helper.sh`, which stay
as they are and run a different set of services (see the root `docker-*` and
`compose-*` make targets).

Layout: `docker/<name>/` holds that service's `Dockerfile`, `docker-compose.yml`
and `.env.example`. Only `docker/server/` exists at this PR; an agent
directory lands per later PR.

## Commands

Run from the repository root.

- `make stack-up C=server` - build and start the server.
- `make stack-up C=all` - start the server, then any other
  `docker/*/docker-compose.yml` (currently none: agents come in later PRs).
- `make stack-down` - stop everything under `docker/`.
- `make stack-status` - `docker compose ps` for each stack.
- `make stack-check` - `GET /connections` from the host and confirm the
  identities in `EXPECTED` (comma-separated) are connected; `EXPECTED=`
  (the default) trivially passes, since no agent is expected yet.

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

`docker/smoke.sh` runs `config -q`, `build`, `up`, a health wait, `stack-check`
and `down` against `docker/server`, each step bounded with `timeout`, and
always brings the stack down on exit. CI does not build or run containers
here, so the author runs this locally and pastes its output into the PR:

```
DERHOST_PUBLISH_HOST=127.0.0.2 docker/smoke.sh
```
