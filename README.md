# Executor

A command dashboard for a self-hosted homelab. It shows every machine and
service with live status, who is watching the media server and from where on a
live map, backups and off-site storage, and runs predefined actions, such as a
full reset of a VPN download stack.

It is designed to be reached only over a private network such as WireGuard,
and it has no login. Everything in the security section follows from that.

## How it is built

One Docker image runs in two roles, as two containers in one stack:

| Container | Role | Can reach |
|---|---|---|
| `executor` | Serves the page and `/api`, probes services and machines | The Docker host, the network, the runner |
| `executor-runner` | Executes actions and reports container states and watched folders; the only container holding the Docker socket | The Docker socket, `executor` over an internal network, and outbound connections for ssh and http steps |

- **Backend:** Python, FastAPI and uvicorn, in `backend/executor/`.
- **Frontend:** React and TypeScript built with Vite, in `web/`. The build is
  served by the backend, so the page and the API share one origin.
- **Configuration** is kept out of the image and out of git:
  - `config.yaml` (services, machines, security) is read by the web container.
  - `actions.yaml` is read only by the runner, so the web container never sees,
    and cannot change, a command.

  Start from `deploy/config/config.example.yaml` and
  `deploy/config/actions.example.yaml`.

### The page

Five decks, switched by tabs (a bottom bar on phones) or the keys 1 to 5:

| Deck | Shows |
|---|---|
| Bridge | The map with live streams, and tiles summarising every other deck, the event log and the weekly recap |
| Engineering | Machines, storage health, DNS, the edge (bandwidth and certificates) and services |
| Holonet | The map explorer, now playing, requests, downloads, the media library, places and viewers |
| Archives | The photo library, backup jobs, file backups and off-site storage |
| Armory | Every action, grouped, and the recent runs |

A status line and a row of alerts stay at the top on every deck. Every card,
row, map marker and alert opens a side drawer with the details and where the
figures come from; the address bar keeps the deck and the open drawer, so a
refresh or a bookmark lands in the same place. The eye button blurs names
(presentation mode, `p`), and the play button starts a demo tour that cycles
the decks with names blurred until someone touches the page. Numbers glide to
new values rather than jumping, and nothing flashes on refresh.

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
| Stack stopped | With stack discovery: the stack's containers were removed but its folder is still there (grey, no alert) |

Machines are pinged over ICMP. The machine marked `local: true` is always up
and reports load, memory and uptime from `/proc`.

### Stack discovery (optional)

With a `discovery` section in `config.yaml`, the service list follows what
actually runs instead of only what is listed:

- The runner reports each container's compose project (stack), service and
  published ports. A stack none of whose containers a configured service lists
  appears on its own, named after the stack, in the `Discovered` group, with a
  link (`link_host`) and a TCP check (`probe_host`) on its lowest published
  port. Labels in its compose file name it instead: `executor.name`,
  `executor.group`, `executor.url`, and `executor.hide: "true"` to leave it out.
- With `stacks_dir` in `actions.yaml` (a folder of one subfolder per stack,
  such as a stack manager's), the runner also reports the subfolder names.
  A stack with a folder but no containers shows as stopped (grey, no alert).
  A configured service whose containers are gone (removed, not just stopped:
  Docker still lists stopped containers, which show as down) shows as stopped
  while its stack folder exists and disappears once the folder is deleted, so
  deleting a stack removes its tile. The stack each container belonged to is remembered
  in the data folder, because a removed container no longer says; a service
  never seen running is matched to a folder named like it or its container
  (compared without case, spaces or dashes).
- New and removed stacks are written to the event log.

Configured services keep their names, groups, links and checks; discovery only
adds around them.

### Settings

The gear button in the header opens the settings page (with a writable data
folder). It lists every service, including discovered and hidden ones:

- Rename a service, move it to another group (or a new one) or change its
  link inline, or select several and move, hide, show or reset them at once.
- Reorder groups, and rename a group, which moves every service in it.

Changes apply at once and survive restarts. They are kept in Executor's own
database, not written to `config.yaml` or compose files, which stay as the
defaults; Reset brings those back, and every change goes to the event log.
Health checks, a service's containers, machines and integrations stay in
`config.yaml`. Actions stay in `actions.yaml` on purpose: whoever can change
an action can run commands as root, so the page never edits them.

### Machine stats (optional, via Beszel)

Machines with a `beszel:` name get a richer card from a
[Beszel](https://beszel.dev) hub: CPU, memory, GPU, ZFS pools with health,
temperatures, network rates, a one-hour sparkline, and history charts. Ranges
up to 30 days are read from Beszel's own records; the 90-day and 1-year
ranges come from Executor's hourly averages (see "History, uptime and the
event log"), seeded from what Beszel keeps. Configure `integrations.beszel.url` and give the web
container a Beszel user with the `readonly` role through `BESZEL_EMAIL` and
`BESZEL_PASSWORD`. Machines without a `beszel:` name show as compact
online/offline cards.

### Edge panel (optional)

Shows TLS certificate expiry for the hosts in `integrations.edge.certificates`
(checked hourly from the web container), and the VPS's outbound bandwidth
against the provider allowance, with a month-end projection and daily bars.

Bandwidth is read from a JSON file at `integrations.edge.bandwidth_url`. On a
Vultr VPS, the intended producer is a small timer on the VPS itself that calls
`GET /v2/account/bandwidth` and `/v2/instances/{id}/bandwidth` with a key that:

- belongs to a Vultr **service user** whose only role holds a custom policy
  with `account.bandwidth.Read` plus the managed **View Servers** policy, and
- is allowlisted for the VPS's own IP only,

so the key can only read figures and only works from the VPS. The VPS serves
the resulting file on a private interface to the Executor host only. Only
outbound transfer is billed; the allowance shown is the account's pooled
credits (instance credits accruing hourly plus the free monthly credits).

### Traffic and shields (optional)

Two more cards in the Edge section, from `integrations.edge.traffic_url`:

- **Public sites:** requests to each site over the last day (health checks
  from Executor and Uptime Kuma counted apart, and the share from your own
  devices: the current public addresses of the edge server's WireGuard
  peers, or else home), server and client errors, response times
  (median and 95th percentile), edge cache hits, data served and where
  visitors are. A site's drawer charts 24 hours, 7 or 30 days.
- **Shields:** failed SSH logins, fail2ban bans, firewall blocks and
  requests for host names the server does not serve (scanners), with where
  they come from, the usernames tried and the ports probed. Its drawer has a
  map of attack origins converging on the edge server.

The file comes from a summariser on the edge server that runs every five
minutes as an unprivileged user allowed to read logs, reads only what was
added to the nginx access and catch-all logs, the firewall log, the fail2ban
log and the SSH journal since its last run, and writes counts by hour for the
last 49 hours as JSON: `sites` (per hour and site: requests, health checks,
status classes, bytes, a request-time histogram, cache hits), `visitor_ips`
and `threat_ips` (per hour, address and count), `threat_hours`,
`threat_tags` (usernames and ports) and `recent_15m`. No paths, queries or
user agents are kept. With `own_from_peers`, the summariser counts requests
from its WireGuard peers' current public addresses as `own` per site and
never sends those addresses; listing peers needs root, so its unit records
them in a pre-start step for each run. Executor places each address with its geolocation
databases, keeps visitors only as city counts (35 days) and attacker
addresses for a week, and keeps hourly totals for 400 days. A site serving
more than 5% server errors over 15 minutes raises an alert; an hour with
over three times the week's average attacks goes to the event log; the weekly
recap counts requests and attacks turned away.

### Media panels (optional)

- **Requests** from Jellyseerr: counts, every request waiting for approval and
  the newest approved ones, with poster, requester and age. Posters come
  through the server (`/api/media/poster/...`), which serves only titles it
  has already seen in its own request list and fetches them from Jellyseerr's
  image proxy, so the page loads nothing from other origins.
- **Downloads**: the Sonarr and Radarr queues (progress, time left, size,
  import warnings) and qBittorrent's overall speeds and torrent counts.

All read-only. Keys come from `JELLYSEERR_API_KEY`, `SONARR_API_KEY`,
`RADARR_API_KEY`, `QBITTORRENT_USERNAME` and `QBITTORRENT_PASSWORD`; leave
one out and that part is skipped. qBittorrent bans an address after repeated
failed logins, so check its password before deploying.

### Photo library (optional, via Immich)

A card on the Archives deck with the library's size (photos against videos),
how many items it holds, uploads per day for the last month, free space on the
library disk, Immich's background jobs (thumbnails, face detection, smart
search and the rest, with anything queued, paused or failed) and whether a
newer Immich release is out. Its drawer adds a year of activity as a calendar
heatmap, by the day items were added or the day they were taken, storage per
person, and the library size over time. New uploads (one event per burst, once
the counts settle), failed jobs and new releases go to the event log, and the
weekly recap counts the photos added.

Create an API key in Immich while logged in as an admin (Account Settings >
API Keys) with only `server.statistics`, `server.storage`,
`server.versionCheck`, `queue.read` and `user.read`, and put it in
`IMMICH_API_KEY`. Statistics and job queues are admin-only in Immich; daily
activity covers the key owner's own account. Anything the key may not read is
left out rather than failing the card. Executor records the library size once
a day, so the growth figures fill in from the first day it runs.

### Media library (optional, with Jellyfin)

A Library card on the Holonet deck: movies, shows and episodes per Jellyfin
library (missing episodes Jellyfin knows of but has no file for are left
out), each library's size on disk, and a strip of recent additions with
posters. Sizes come from the runner, which measures the folders listed under
`sizes:` in `actions.yaml` (inside `LIBRARY_DIR`, mounted read-only at
`/library`) and reports only their total size and file count; map Jellyfin
libraries to them with `integrations.jellyfin.library_folders`. Jellyfin
itself knows the file size of only some items. Posters are proxied like
request posters, for the recent additions only. Executor records the totals
daily for a growth chart.

### Storage health (optional, via TrueNAS)

A Storage section on the Engineering deck: each pool's state, usable space,
last scrub and its errors, layout, and its disks with their temperatures;
disks outside data pools; TrueNAS's own alerts; and usage per dataset. Pool,
disk and TrueNAS drawers add the details, including each disk's average and
highest temperature over the last week. A pool that is not online or whose
scrub found errors, a hot disk (hard drives from 50 °C, SSDs from 70 °C) and
TrueNAS warnings raise alerts; pool changes, finished scrubs and new TrueNAS
alerts go to the event log.

Create a TrueNAS user with the Read-only Admin role (password and shell
off) and an API key for it, and put the key in `TRUENAS_API_KEY`. Executor
speaks TrueNAS's JSON-RPC API over `wss://`: TrueNAS revokes any API key that
is sent over plain HTTP. TrueNAS 25.10 no longer offers SMART results
through its API; a failing disk shows as a TrueNAS alert.

### DNS (optional, via Pi-hole)

A DNS card on the Engineering deck: queries and blocked queries today, the
share blocked, cache hits, active clients, blocklist size, the most blocked
domains and a 24-hour chart; the drawer adds the busiest clients (their names
blur in presentation mode). Pi-hole's blocking switched off or on goes to the
event log, and an alert shows while it is off. Pi-hole v6 has no read-only
login: create an app password (Settings > Web interface / API, Expert mode)
and put it in `PIHOLE_PASSWORD`. Executor only reads, and logs out of its
session when it stops.

### Now playing and location history (optional)

With the `jellyfin` integration, the Holonet deck shows who is watching
what, from which city and address, with progress and whether the stream is
transcoding. With `history_days` set and a writable `EXECUTOR_WEB_DATA`
folder, Executor also keeps a location history:

- Jellyfin's sessions are sampled every 30 seconds, and its activity log
  (which records the address of every session start for about a month) is
  imported, so history starts with what Jellyfin already knows.
- Each address is looked up in the free DB-IP Lite City database, downloaded
  monthly into the data folder. Lookups are local; addresses are never sent
  to any other service. Accuracy is city level at best: mobile carriers often
  place users in a hub city, and VPN users appear at the VPN's exit. The page
  credits DB-IP as its CC BY 4.0 licence requires.
- Two free databases, both downloaded and used locally: MaxMind GeoLite2
  City first, when `MAXMIND_ACCOUNT_ID` and `MAXMIND_LICENSE_KEY` are set (a
  free account; refreshed weekly; it also estimates its own error, drawn as a
  faint circle), then DB-IP Lite. When they name different cities, the
  stream's details show the second opinion.
- Places you know beat any database: `corrections` in `config.yaml` match
  one user's device (`user_devices`, which wins even over the home address),
  a Jellyfin device name, a network, or a user who is always in one place;
  `home: true` places the match at `origin`. With `home_ip_url` the server
  learns its own public address hourly and places sessions from it at
  `origin`. Behind carrier-grade NAT every device at home may get its own
  public address from a shared block: `household` lists users whose sessions
  from the same block as the server's address (`prefix_v4` leading bits)
  also count as home. When a database update or a changed correction could
  move places, the whole history is located again (sessions placed at home
  stay there).
- History older than `history_days` is deleted daily.
- `GET /api/media/users`, `/api/media/places` and `/api/media/trail` serve
  the history to the page.

**The map.** A vector map (MapLibre GL) of country and state borders, cities,
towns, roads and water, drawn from map tile archives that Executor serves
itself (see "Map tiles"), so the page loads nothing from other sites. It pans,
zooms and pinches; zoom buttons, "fit everything" and "fly home" sit in the
corner. Dots flow from `origin` (where the media server is) to `hub` (the
relay, if any) and on to each viewer; with `machine` set on them, the two
anchors are ringed with that machine's health. Two views:

- **Live** fits the stream route and whoever is watching.
- **All places** shows every place in the chosen range (7, 30 or 90 days),
  clustered when zoomed out, with the busiest places named.

Hover anything for a summary and click it for details: a viewer or their line
(who, what, from where, on which app, transcoding or not), the route, a
place (everyone seen there), or an anchor (its machine). Picking a viewer
draws their numbered path, which can be replayed step by step.

### Map tiles

The map reads `.pmtiles` archives from the stack's `tiles/` folder (mounted
read-only at `/tiles`): `world.pmtiles` for the whole world, and optionally
more archives with street-level detail for a region, drawn on top where they
have tiles. Make them once with the `pmtiles` tool from the Protomaps daily
build (only the requested parts are downloaded):

```bash
pmtiles extract https://build.protomaps.com/<YYYYMMDD>.pmtiles world.pmtiles --maxzoom=10
pmtiles extract https://build.protomaps.com/<YYYYMMDD>.pmtiles region.pmtiles --bbox=<w,s,e,n> --minzoom=11 --maxzoom=14
```

The world to zoom 10 is about 4 GB; a country-sized region from zoom 11 to 14
is a few GB more. Fonts and icons come from Protomaps' basemaps-assets at a
pinned commit, fetched when the image is built. Map data © OpenStreetMap
contributors, basemap by Protomaps.

### History, uptime and the event log

With a writable `EXECUTOR_WEB_DATA` folder, Executor keeps its own records in
one SQLite file:

- **Event log:** services and machines going down and recovering, containers
  stopping or restarting, backups finishing, downloads grabbed and finished,
  new requests, streams starting, actions run, certificates renewed, photos
  added to the library and Immich jobs failing, pool changes, scrubs and
  TrueNAS alerts, and Pi-hole's blocking switched off or on. A
  service change counts once it holds for two checks. Kept 180 days.
- **Uptime:** every service check in five-minute buckets, shown as uptime bars
  in each service's details. Kept 35 days.
- **Machine history:** hourly averages, seeded from what Beszel still keeps
  (about a month), so machine charts reach back a year (90-day and 1-year
  ranges).
- **Plays:** what was played, by whom and for how long, from Jellyfin's
  activity log, and the bytes qBittorrent downloads each day, for the
  weekly recap (hours streamed, top titles, viewers and cities, downloads,
  uptime, incidents and Glacier growth, against the week before).
- **Photo library size:** one reading a day of the Immich library's size and
  counts, for its growth chart. Kept 400 days.
- **Media library size:** one reading a day of the Jellyfin libraries'
  counts and sizes, for their growth chart. Kept 400 days.
- **Edge traffic and attacks:** hourly totals per public site and of
  attacks (400 days), visitor places (35 days), attacker addresses (7 days).

Without the folder the event log is kept in memory only, and the recap,
uptime bars and long ranges are unavailable.

**Presentation mode** (the eye button in the header) blurs every username,
and device names (which often contain a person's name), for showing the
dashboard to others. Locations and addresses stay visible. The choice is
remembered per browser.

### Backups panel (optional)

One card per backup, with its state (OK, warnings, failed, overdue, running),
when it last finished, and what comes next.

- **Duplicati jobs** are read from Duplicati's API with the UI password
  (`DUPLICATI_PASSWORD`), because Duplicati has no read-only login. Each card
  shows the last run's duration and added data, a strip of the last ten
  results, source and stored sizes, versions kept, and the next scheduled run.
  A job is overdue when its last run is older than its repeat interval plus a
  margin. Point `integrations.backups.duplicati.url` at an IP address:
  Duplicati refuses host names that are not on its allowlist, including
  `host.docker.internal`. Destinations, which can hold storage credentials, are never read
  into the result.
- **File-based backups**, such as nightly database dumps, are judged by the
  files they leave behind. Such folders are often readable only by root, so
  the runner reports them: `files:` in `actions.yaml` names each folder (inside
  `WATCH_DIR`, mounted read-only at `/watch`), and the runner returns only file
  names, sizes and times, plus the last lines of the files listed under
  `tail`. It never follows symlinks. `config.yaml` then says how to judge each
  folder: `expect` (a fixed set of files that must all be fresh and non-empty)
  or `pattern` (the newest of a rotating set), `max_age_hours`, an optional
  `error_file` whose content means failure, and an optional `log_file` whose
  last line is shown.
- **S3 storage** (for example a Glacier archive): size, 30- and 90-day
  growth, a size-over-time chart, object count and an estimated monthly cost,
  from the daily storage metrics S3 publishes to CloudWatch (kept for 15
  months, so the chart has history from the start). The key needs only
  `cloudwatch:GetMetricStatistics`, which reads numbers and cannot touch any
  data; that action accepts no resource or condition limits in IAM. Requests
  are signed with SigV4 using the standard library. Without a key, the card
  shows the linked Duplicati job's stored size and estimate instead.

### Actions

An action is an ordered list of steps in `actions.yaml`. A step is exactly one
of:

| Step | Does |
|---|---|
| `run` | Runs an argument list (never a shell string) in the runner |
| `wait_healthy` | Waits until Docker reports a container healthy |
| `ssh` | Runs a named command on an SSH target (see below) |
| `http` | Makes an API call; `${NAME}` in the URL and header values is filled from the runner's environment and never logged |

The runner stops at the first failure and skips the rest. `retry_every`
repeats a failing `run`, `ssh` or `http` step until its timeout, which is how
an action waits for something, such as a host coming back after a reboot.
Only one action runs at a time. Every run, with its full output, is appended
to `runs.jsonl` in the runner's data directory and listed under "Recent runs".

Launching takes a press and hold: the ring fills for 0.7, 1.4 or 2.6 seconds
by `danger` (low, medium, high), and letting go early cancels. The run then
shows as a pipeline whose steps light up as they go, with their times and the
output. `group` sets the action's heading in the Armory, and `attach` also
offers it on the cards it concerns (machine or service ids, or the panels
`downloads`, `requests`, `now-playing`, `backups`, `edge` and
`backup-<Duplicati job id>`).

An action with `show_streams: true` lists the media server's active streams
in its briefing, so you can see who would be interrupted. This needs the
`jellyfin` integration in `config.yaml` and `JELLYFIN_API_KEY` in `.env`.

The runner gets `JELLYFIN_API_KEY`, `SONARR_API_KEY`, `RADARR_API_KEY` and
`PROWLARR_API_KEY` from `.env` for `http` steps; add any other key to its
`environment` in `compose.yaml`. Variables that `http` steps read are removed
from the environment of every command the runner starts, like `RUNNER_TOKEN`.
Run output is scrubbed before it is shown or saved: the values of those
secrets, and credentials in `key=value` form (`apikey=`, `token=`,
`password=` and the like), which apps often echo in error messages about
other apps, become `<redacted>`. History written by older versions is
scrubbed the same way when the runner starts.

#### SSH steps

An `ssh` step names a target from the `ssh:` section of `actions.yaml` and a
command, which must be a plain word such as `nginx-reload`. The runner uses
the key and `known_hosts` in `$EXECUTOR_DIR/ssh/` (mounted read-only at
`/ssh`), with strict host key checking and no SSH config file.

The command name is not trusted on the remote side. Give the key its own
account there and lock it to a dispatcher in that account's
`authorized_keys`:

```
restrict,from="<executor host>",command="/usr/local/bin/executor-dispatch" ssh-ed25519 AAAA... executor-runner
```

`restrict` turns off forwarding and terminals, `from=` accepts the key only
from the Executor host, and `command=` replaces whatever the client asked for
with the dispatcher, which receives the requested name in
`SSH_ORIGINAL_COMMAND`. The dispatcher accepts only a fixed list of names. For
anything privileged, it calls a root-owned helper through `sudo`, with a
sudoers rule that allows that helper with exactly those arguments. Make the
account's home and `.ssh` directory root-owned, so the account cannot change
its own key options.

A long job, such as a system upgrade followed by a reboot, should be started
by the dispatcher in its own unit (for example with `systemd-run`), so it
survives the SSH session ending. A second dispatcher command can then follow
its log, and a third can report whether the host has rebooted since, for a
step with `retry_every`.

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
- **Docker socket isolation.** Only the runner holds the socket. The web
  container reaches it only over an internal network, it publishes no port, and
  every request needs a shared bearer token (`RUNNER_TOKEN`), which is
  stripped from the environment of every command it runs. The runner also has
  a network of its own for the outbound connections of `ssh` and `http` steps; holding the
  Docker socket, it could reach the network through a new container anyway.
- **The web container never names a path.** The folders the runner may report
  on are fixed in `actions.yaml`, like the commands, and the runner reports
  only file names, sizes, times and the tails of files listed there.
- **Remote hosts trust names, not commands.** An `ssh` step's key should work
  only from the Executor host and only through a dispatcher that accepts a
  fixed list of names (see "SSH steps").
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
   For `ssh` steps, create `ssh/` beside them with a key pair
   (`ssh-keygen -t ed25519 -N "" -f ssh/id_ed25519`) and a `known_hosts` file
   holding each target's host key, checked against the target itself.
4. For the map, put the tile archives in `tiles/` beside the configs (see
   "Map tiles").
5. Run `docker compose up -d` in that directory, or deploy it with your stack
   manager.
6. Open `http://<allowed host>:1977` from an allowed device.

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
To work on the frontend against a deployed instance instead, start it with
`EXECUTOR_API=http://<executor host>:1977 npm run dev` from an allowed device;
adding `EXECUTOR_LOCAL_API=http://127.0.0.1:1977` serves the history endpoints
and map tiles from the local backend. For a local map, extract small archives
(for example `--maxzoom=6`) into `dev/tiles/` and start the web process with
`EXECUTOR_TILES=../dev/tiles`.
Without a Docker socket the page notes that container states are unavailable,
which is expected. `dev/actions.yaml` holds two harmless demo actions.

Tests: `cd backend && ../.venv/bin/python -m pytest -q`. Tests that check a real
`config.yaml` run only where one exists.

## Roadmap

1. **Phase 1:** status for every service and machine, and one-click actions
   (done).
2. **Phase 2:** machine stats and history from Beszel (done);
   bandwidth and certificate expiry (done); more actions, with ssh and http
   steps and an active-stream warning (done); backups panel (done); media
   panels (done); S3 storage size and growth (done).
3. **Phase 3:** now playing, location history, presentation mode, and a map
   of locations with a per-user timeline (done).
4. **Phase 4:** decks instead of one long page, drawers for every detail, a
   self-hosted vector map, hold-to-launch actions with a live pipeline, the
   event log, uptime history, a year of machine history, the weekly recap and
   the demo tour (done); stack discovery, so the service list follows the
   stacks that actually run (done); the settings page (done); the Immich photo
   library (done); the media library, storage health and DNS (done); edge
   traffic and shields (done).
