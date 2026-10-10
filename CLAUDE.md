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
  `ssh` steps send only a command name; the remote side's forced-command
  dispatcher decides what it means.
- **Only the runner touches the Docker socket.** The web container stays
  non-root, read-only and capability-free.
- **Every non-GET request must pass the guard in `backend/executor/security.py`.**
  Frontend calls that change anything send `X-Executor: 1` and JSON.
- **Service keys stay server-side** in the stack's `.env`; the browser never
  receives them.
- **The page loads nothing from other origins** (CSP `default-src 'self'`).
  Map tiles are `.pmtiles` archives in the stack's `tiles/` folder, served by
  the web container; map fonts and icons are fetched at build time by
  `web/scripts/map-assets.sh` (pinned commit) into `web/public/map/`. Never
  commit tiles or map assets, and never point the map at a hosted tile service.
- **The runner reports names, never contents or paths from requests.** Watched
  folders (`files:`), sized folders (`sizes:`) and the stacks folder
  (`stacks_dir:`) are fixed in `actions.yaml`; it reports file names, sizes,
  times, configured log tails, folder totals and stack folder names only.
  Run output is scrubbed of secret values and `key=value` credentials.
- **The settings page changes presentation only** (service names, groups,
  links, visibility, group order), stored in the web data folder. Never let it
  edit actions, checks that run commands, or anything the runner reads.
- **Frontend layout:** decks in `web/src/decks/`, one drawer for every detail
  (`components/Drawer.tsx`, routed through the URL hash in `route.ts`), the
  map in `web/src/map/`. New clickable things open a drawer kind rather than a
  modal.
- **One visual language:** statuses and tags use `components/Badge.tsx`, card
  headings the `.card-head` pattern, actions `AttachedActions` (one row at the
  foot of a card), time ranges `components/RangePicker.tsx`, deck sections
  `DeckSection` (foldable). Reuse them rather than adding new variants.
- **Keep the page light on phones:** no CSS animation that runs forever, no
  `backdrop-filter` on touch screens, the map animates only while something is
  live and on screen, relative times read `useClock()` (not a per-second
  clock), and object props to the map go through `useStable`.
- Before committing: `cd backend && ../.venv/bin/python -m pytest -q`,
  `cd web && npm run build`, and check `git status` lists no private file.
