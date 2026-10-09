# Executor: rules for working in this repo

Read `README.md` first; its "Security model" section is binding. If present, also
read the git-ignored `PLAN.local.md` (plan, decisions, state and history) and
`CLAUDE.local.md` (machine-specific details and deployment steps).

- **This repo and its image are public.** Never commit anything that describes a
  real network: addresses, domains, hostnames, host paths, service lists. Those
  belong only in the git-ignored files (`deploy/config/config.yaml`,
  `deploy/config/actions.yaml`, `dev/config.yaml`, `.env`, `*.local.md`).
  Examples and tests use placeholder addresses (`10.8.0.0/24`, `192.168.0.0/24`)
  and `example.com`.
- **Never bake configuration or secrets into the image.** `.dockerignore`
  excludes `deploy/` and `dev/`; keep it that way.
- **Private network only, no login, by the owner's decision.** Never add a public
  reverse proxy site, a login page, or anything that assumes public exposure
  without being asked.
- **Commands live only in `actions.yaml`, read only by the runner.** Never add an
  endpoint that accepts a command, a shell string, or a path from a request.
- **Only the runner touches the Docker socket.** The web container stays
  non-root, read-only and capability-free.
- **Every non-GET request must pass the guard in `backend/executor/security.py`.**
  Frontend calls that change anything send `X-Executor: 1` and JSON.
- **Service keys, when later phases need them, stay server-side** in the stack's
  `.env`; the browser never receives them.
- Before committing: `cd backend && ../.venv/bin/python -m pytest -q`,
  `cd web && npm run build`, and check `git status` lists no private file.
