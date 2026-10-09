# Executor

A single-page command dashboard for a self-hosted homelab. One page shows every
machine and service with live status, and runs predefined actions with a click,
such as a full reset of a VPN download stack.

It is designed to be reached only over a private network such as WireGuard,
and it has no login. Everything in the security section follows from that.

## How it is built

One Docker image runs in two roles, as two containers in one stack:

| Container | Role | Can reach |
|---|---|---|
| `executor` | Serves the page and `/api`, probes services and machines | The Docker host, the network, the runner |
| `executor-runner` | Executes actions; the only container holding the Docker socket | The Docker socket, and `executor` over an internal network |

- **Backend:** Python, FastAPI and uvicorn, in `backend/executor/`.
- **Frontend:** React and TypeScript built with Vite, in `web/`. The build is
  served by the backend, so the page and the API share one origin.
- **Configuration** is kept out of the image and out of git:
  - `config.yaml` (services, machines, security) is read by the web container.
  - `actions.yaml` is read only by the runner, so the web container never sees,
    and cannot change, a command.

  Start from `deploy/config/config.example.yaml` and
  `deploy/config/actions.example.yaml`.

### Status

Every service has an optional probe (HTTP or TCP, run from inside the
container, usually against `host.docker.internal`) and an optional list of
containers. The first container is the primary one.

| Status | Meaning |
|---|---|
| Up | The probe passed and every listed container is running |
| Degraded | The probe passed, but a secondary container is stopped, missing, unhealthy or starting |
| Down | The probe failed, or the primary container is not running |
| Unknown | No probe has run yet and container states are unavailable |

Machines are pinged over ICMP. The machine marked `local: true` is always up
and reports load, memory and uptime from `/proc`.

### Machine stats (optional, via Beszel)

Machines with a `beszel:` name get a richer card from a
[Beszel](https://beszel.dev) hub: CPU, memory, GPU, ZFS pools with health,
temperatures, network rates, a one-hour sparkline, and a history view (1 hour
to 30 days) drawn from Beszel's own records. Executor never stores this
history; it reads it. Configure `integrations.beszel.url` and give the web
container a Beszel user with the `readonly` role through `BESZEL_EMAIL` and
`BESZEL_PASSWORD`. Machines without a `beszel:` name show as compact
online/offline cards.

### Actions

An action is an ordered list of steps in `actions.yaml`. A step is either `run`
(an argument list, never a shell string) or `wait_healthy` (a container name).
The runner stops at the first failure and skips the rest. Only one action runs
at a time. Every run, with its full output, is appended to `runs.jsonl` in the
runner's data directory and listed under "Recent runs".

## Security model

There is no login, so the network decides who gets in. The server also guards
against the one thing a network rule cannot stop: a malicious web page open in
an allowed browser.

- **Client allowlist and denylist.** Only addresses in
  `security.allowed_clients` are served. `security.denied_clients` is checked
  first and always wins. Deny the WireGuard hub, because it faces the internet,
  and the home LAN, because of guests and IoT devices. Allow only your own
  devices' WireGuard addresses.
- **Host check.** The `Host` header must be one of `security.allowed_hosts`,
  which defeats DNS rebinding.
- **Cross-site request guard.** Anything other than GET or HEAD must send
  `X-Executor: 1` and `Content-Type: application/json`, and is refused if its
  `Origin` or `Sec-Fetch-Site` points elsewhere. A cross-site page cannot set
  that header without a CORS preflight, and the server answers no preflights.
- **Docker socket isolation.** Only the runner holds the socket. It sits on an
  internal-only network and requires a shared bearer token (`RUNNER_TOKEN`),
  which is stripped from the environment of every command it runs.
- **Hardened web container.** Non-root user, read-only filesystem, all
  capabilities dropped, `no-new-privileges`, and a strict Content Security
  Policy.
- **Do not publish it.** Do not put Executor behind a public reverse proxy. If
  the proxy is the denied WireGuard hub, the guard refuses its traffic anyway.
- **Lost device.** A lost phone or laptop is a lost key: remove its peer from
  the WireGuard hub.
- **Config files are root-equivalent.** Whoever can edit `actions.yaml` can run
  commands as root through the runner, just as whoever can edit any stack's
  compose file already can. Keep the stack directory's permissions in mind.
- **The image is public and generic.** It contains no configuration, addresses
  or secrets. Whoever can push to the image's registry controls any host that
  runs the runner, so protect the GitHub account that publishes it.

## Deploying

The image is built by GitHub Actions on every push to `main` and published to
`ghcr.io/lexical-yoda/executor`.

1. Create a directory for the stack and copy `deploy/compose.yaml` into it.
2. Copy `deploy/.env.example` to `.env` beside it, set `RUNNER_TOKEN` to the
   output of `openssl rand -hex 32`, set the paths, and `chmod 600 .env`.
3. Copy the two example configs into `config/` as `config.yaml` and
   `actions.yaml`, and describe your own network, services and actions.
4. Run `docker compose up -d` in that directory, or deploy it with your stack
   manager.
5. Open `http://<allowed host>:1977` from an allowed device.

To update, pull the new image and recreate the stack. Editing `config.yaml` or
`actions.yaml` needs only a restart of the matching container.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
cd web && npm install && npm run build && cd ..
cp dev/config.example.yaml dev/config.yaml   # then point the probes at real services
export RUNNER_TOKEN=$(openssl rand -hex 32)
cd backend
EXECUTOR_ACTIONS=../dev/actions.yaml RUNNER_DATA=/tmp/executor-runner DOCKER_SOCKET=/nonexistent ../.venv/bin/python -m executor runner &
EXECUTOR_CONFIG=../dev/config.yaml RUNNER_URL=http://127.0.0.1:8001 EXECUTOR_STATIC=../web/dist ../.venv/bin/python -m executor web
```

Then open `http://127.0.0.1:1977`. For live frontend reloading, also run
`npm run dev` in `web/` and open the Vite URL; it proxies `/api` to port 1977.
Without a Docker socket the page notes that container states are unavailable,
which is expected. `dev/actions.yaml` holds two harmless demo actions.

Tests: `cd backend && ../.venv/bin/python -m pytest -q`. Tests that check a real
`config.yaml` run only where one exists.

## Roadmap

1. **Phase 1:** status for every service and machine, and one-click actions.
2. **Phase 2 (in progress):** machine stats and history from Beszel (done);
   bandwidth and certificate expiry, media panels, backups panel, and more
   actions, including a VPS update and reboot.
3. **Phase 3:** a media section: now playing, active users, a globe of login
   locations, and per-user location history.
