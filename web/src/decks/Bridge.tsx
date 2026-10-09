import { Archive, ArrowDown, CalendarDays, Cpu, Map as MapIcon, Radio, ScrollText, Thermometer, Zap } from 'lucide-react'
import { lazy, Suspense, useMemo } from 'react'
import type { MachineStatus, Snapshot, Status } from '../api'
import { ago, bytes, pct, rate } from '../format'
import { streamKey } from '../map/types'
import { useActions, useApp } from '../state'
import { runClock, runSeconds } from '../components/ActionKit'
import { BackupPill } from '../components/Backups'
import { gb } from '../components/Edge'
import { FeedList, useEvents } from '../components/Feed'
import { ICONS } from '../components/Machines'
import { placeName, Poster } from '../components/Media'
import { RecapHighlights, RecapStats, useRecap } from '../components/Recap'
import { StatusDot } from '../components/StatusDot'
import { Empty, Num, RowButton, Tile } from '../components/ui'

const MapView = lazy(() => import('../map/MapView'))

// Machines that come and go all day are listed last and never alarm.
const QUIET = new Set(['laptop', 'phone'])

export function nodeStatuses(s: Snapshot | null): Record<string, Status> {
  return Object.fromEntries((s?.machines ?? []).map((m) => [m.id, m.status]))
}

function MiniBar({ value }: { value: number | null }) {
  const tone = value == null ? 'unknown' : value >= 90 ? 'down' : value >= 70 ? 'degraded' : 'up'
  return (
    <span className="mini-bar" title={pct(value)}>
      <span className={`mini-fill bar-${tone}`} style={{ width: `${Math.min(100, value ?? 0)}%` }} />
    </span>
  )
}

function MachineRow({ m }: { m: MachineStatus }) {
  const { open, now } = useApp()
  const Icon = ICONS[m.icon]
  const s = m.stats
  return (
    <RowButton className="machine-row" onClick={() => open('machine', m.id)}>
      <StatusDot status={m.status} />
      <Icon size={14} className="muted" />
      <span className="machine-row-name">{m.name}</span>
      {s ? (
        <span className="machine-row-stats small">
          <span className="mini-stat">
            <span className="muted">CPU</span>
            <MiniBar value={s.cpu_pct} />
          </span>
          <span className="mini-stat">
            <span className="muted">MEM</span>
            <MiniBar value={s.mem_pct} />
          </span>
          {s.cpu_temp != null && (
            <span className="mini-stat muted">
              <Thermometer size={11} />
              <Num value={s.cpu_temp} format={(v) => `${Math.round(v)}°`} />
            </span>
          )}
        </span>
      ) : (
        <span className="small muted">
          {m.status === 'up' ? (m.latency_ms != null ? `${Math.round(m.latency_ms)} ms` : 'online') : m.last_seen ? `seen ${ago(m.last_seen, now)}` : 'offline'}
        </span>
      )}
    </RowButton>
  )
}

function EngineeringTile({ s }: { s: Snapshot }) {
  const { showDeck, open } = useApp()
  const machines = [...s.machines].sort((a, b) => Number(QUIET.has(a.icon)) - Number(QUIET.has(b.icon)))
  const down = s.services.filter((x) => x.status !== 'up' && x.status !== 'unknown')
  const cert = s.edge?.certificates.reduce<number | null>(
    (min, c) => (c.days_left != null && (min == null || c.days_left < min) ? c.days_left : min),
    null,
  )
  const bw = s.edge?.bandwidth
  return (
    <Tile
      title="Engineering"
      subtitle={`${s.summary.machines_up}/${s.summary.machines_total} machines online`}
      icon={<Cpu size={15} />}
      onOpen={() => showDeck('engineering')}
      tone={down.some((x) => x.status === 'down') ? 'down' : down.length ? 'degraded' : 'up'}
      className="tile-engineering"
    >
      <div className="machine-rows">
        {machines.map((m) => (
          <MachineRow key={m.id} m={m} />
        ))}
      </div>
      <div className="tile-foot">
        <button type="button" className="foot-stat" onClick={() => showDeck('engineering')}>
          <Num value={s.summary.services_up} />/{s.summary.services_total} <span className="muted">services up</span>
        </button>
        {s.summary.containers_known && (
          <span className="foot-stat">
            <Num value={s.summary.containers_running} />/{s.summary.containers_total} <span className="muted">containers</span>
          </span>
        )}
        {cert != null && (
          <span className="foot-stat">
            <span className="num">{Math.floor(cert)}d</span> <span className="muted">to cert expiry</span>
          </span>
        )}
        {bw?.used_pct != null && (
          <button type="button" className="foot-stat" onClick={() => open('bandwidth')}>
            <span className="num">{gb(bw.out_gb)}</span> <span className="muted">VPS out ({Math.round(bw.used_pct)}%)</span>
          </button>
        )}
      </div>
      <div className="group-health">
        {s.groups.map((group) => {
          const items = s.services.filter((x) => x.group === group)
          if (!items.length) return null
          return (
            <RowButton key={group} onClick={() => showDeck('engineering')} title={`${group}: ${items.filter((x) => x.status === 'up').length} of ${items.length} up`}>
              <span className="group-name">{group}</span>
              <span className="group-dots">
                {items.map((x) => (
                  <StatusDot key={x.id} status={x.status} />
                ))}
              </span>
            </RowButton>
          )
        })}
      </div>
      {down.length > 0 && (
        <ul className="tile-issues">
          {down.slice(0, 3).map((x) => (
            <li key={x.id}>
              <RowButton onClick={() => open('service', x.id)}>
                <StatusDot status={x.status} /> {x.name} <span className="small muted">{x.error ?? x.status}</span>
              </RowButton>
            </li>
          ))}
        </ul>
      )}
    </Tile>
  )
}

function HolonetTile({ s }: { s: Snapshot }) {
  const { showDeck, open } = useApp()
  const req = s.media?.requests
  const dl = s.media?.downloads
  const t = dl?.torrents
  const active = dl?.queue.filter((q) => q.status === 'downloading') ?? []
  return (
    <Tile
      title="Holonet"
      subtitle="requests and downloads"
      icon={<Radio size={15} />}
      onOpen={() => showDeck('holonet')}
      className="tile-holonet"
    >
      <div className="tile-stats">
        <div className="tile-stat">
          <Num value={s.jellyfin?.watching.length ?? 0} className="tile-big" />
          <span className="small muted">watching</span>
        </div>
        <div className="tile-stat">
          <Num value={req?.counts.pending ?? null} className="tile-big" />
          <span className="small muted">to approve</span>
        </div>
        <div className="tile-stat">
          <Num value={dl?.queue.length ?? null} className="tile-big" />
          <span className="small muted">in queue</span>
        </div>
        {t?.ok && (
          <div className="tile-stat">
            <span className="tile-big tile-big-small">
              <ArrowDown size={14} />
              <Num value={t.down_bps ?? 0} format={rate} />
            </span>
            <span className="small muted">download speed</span>
          </div>
        )}
      </div>
      {req && req.pending.length > 0 && (
        <div className="poster-strip">
          {req.pending.slice(0, 6).map((r) => (
            <button key={r.id} type="button" className="poster-btn" onClick={() => open('request', String(r.id))} title={r.title}>
              <Poster item={r} />
            </button>
          ))}
        </div>
      )}
      {active.slice(0, 2).map((q) => (
        <RowButton key={q.id} className="tile-download" onClick={() => open('download', q.id)}>
          <span className="tile-download-title">
            {q.title} {q.subtitle && <span className="muted small">{q.subtitle}</span>}
          </span>
          <span className="queue-bar">
            <span className="queue-fill queue-active" style={{ width: `${Math.round((q.progress ?? 0) * 100)}%` }} />
          </span>
        </RowButton>
      ))}
      {!active.length && !req?.pending.length && <Empty>Hyperspace lanes are clear.</Empty>}
    </Tile>
  )
}

function ArchivesTile({ s }: { s: Snapshot }) {
  const { showDeck, open, now } = useApp()
  const b = s.backups
  if (!b) return null
  const store = b.storage[0]
  const worst = [...b.duplicati.jobs.map((j) => j.status), ...b.files.map((f) => f.status)]
  const tone = worst.some((x) => x === 'failed' || x === 'missing') ? 'down' : worst.some((x) => x === 'stale' || x === 'warning') ? 'degraded' : 'up'
  return (
    <Tile title="Archives" subtitle="backups" icon={<Archive size={15} />} onOpen={() => showDeck('archives')} tone={tone} className="tile-archives">
      <ul className="archive-rows">
        {b.duplicati.jobs.map((j) => (
          <li key={j.id}>
            <RowButton onClick={() => open('backup', j.id)}>
              <span className="archive-name">{j.name}</span>
              <span className="small muted">{j.last_finished ? ago(j.last_finished * 1000, now) : 'never'}</span>
              <BackupPill status={j.status} />
            </RowButton>
          </li>
        ))}
        {b.files.map((f) => (
          <li key={f.name}>
            <RowButton onClick={() => open('files', f.name)}>
              <span className="archive-name">{f.name}</span>
              <span className="small muted">{f.last ? ago(f.last * 1000, now) : 'never'}</span>
              <BackupPill status={f.status} />
            </RowButton>
          </li>
        ))}
      </ul>
      {store && (
        <RowButton className="tile-storage" onClick={() => open('storage', store.name)}>
          <span className="small muted">{store.name}</span>
          <Num value={store.aws?.bytes ?? store.duplicati_bytes} format={(v) => bytes(v)} className="tile-big" />
          {store.aws?.growth_30d != null && <span className="small muted">+{bytes(store.aws.growth_30d)} in 30 days</span>}
        </RowButton>
      )}
    </Tile>
  )
}

function ArmoryTile() {
  const { showDeck, open, now } = useApp()
  const { actions, runs, busy } = useActions()
  const running = busy ? runs.find((r) => r.id === busy) : null
  const recentIds = [...new Set(runs.map((r) => r.action))]
  const quick = [
    ...recentIds.map((id) => actions?.find((a) => a.id === id)).filter(Boolean),
    ...(actions ?? []).filter((a) => !recentIds.includes(a.id)),
  ].slice(0, 4)
  const last = runs[0]
  return (
    <Tile title="Armory" subtitle={`${actions?.length ?? 0} actions ready`} icon={<Zap size={15} />} onOpen={() => showDeck('armory')} className="tile-armory">
      {running ? (
        <RowButton className="armory-running" onClick={() => open('run', running.id)}>
          <span className="small">Running</span>
          <span>{running.title}</span>
          <span className="num small">{runClock(runSeconds(running.started_at, null, now))}</span>
        </RowButton>
      ) : last ? (
        <RowButton onClick={() => open('run', last.id)}>
          <span className={`run-dot run-${last.status}`}>{last.status}</span>
          <span>{last.title}</span>
          <span className="small muted">{ago(last.started_at, now)}</span>
        </RowButton>
      ) : (
        <Empty>No actions run yet.</Empty>
      )}
      <div className="quick-actions">
        {quick.map((a) => (
          <button key={a!.id} type="button" className={`action-chip danger-${a!.danger}`} onClick={() => open('action', a!.id)}>
            <Zap size={12} /> {a!.title}
          </button>
        ))}
      </div>
    </Tile>
  )
}

function FeedTile() {
  const { open } = useApp()
  const { events, error } = useEvents()
  return (
    <Tile title="Ship's log" icon={<ScrollText size={15} />} onOpen={() => open('feed')} className="tile-feed">
      {error && <p className="small warn-text">{error}</p>}
      {events ? <FeedList events={events} limit={9} /> : <Empty>Loading the log…</Empty>}
    </Tile>
  )
}

function RecapTile({ ledger }: { ledger: boolean }) {
  const { open } = useApp()
  const { data, error } = useRecap(7)
  return (
    <Tile title="This week" icon={<CalendarDays size={15} />} onOpen={() => open('recap')} className="tile-recap">
      {!ledger && <Empty>The weekly recap needs Executor's data folder.</Empty>}
      {ledger && error && <p className="small warn-text">{error}</p>}
      {ledger && data && (
        <>
          <RecapStats data={data} />
          <RecapHighlights data={data} />
        </>
      )}
    </Tile>
  )
}

export function Bridge({ tour }: { tour: boolean }) {
  const { snapshot, open, route } = useApp()
  const s = snapshot!
  const j = s.jellyfin
  const statuses = useMemo(() => nodeStatuses(snapshot), [snapshot])
  const selected = route.drawer?.kind === 'stream' ? route.drawer.id : null
  const watching = j?.watching ?? []

  return (
    <div className="bridge">
      {j && (
        <section className="hero card">
          <Suspense
            fallback={
              <div className="map-loading">
                <MapIcon size={22} className="spin-slow" />
              </div>
            }
          >
            <MapView
              variant="hero"
              mode="live"
              origin={j.origin}
              hub={j.hub}
              nodeStatus={statuses}
              live={watching}
              places={[]}
              trail={[]}
              replay={null}
              selected={selected}
              onOpen={(kind, id) => open(kind, id)}
              tour={tour}
              overlay={
                <div className="hero-overlay">
                  <div className="hero-badge">
                    <Radio size={14} className={watching.length ? 'pulse-icon' : ''} />
                    <Num value={watching.length} className="hero-count" />
                    <span>{watching.length === 1 ? 'transmission' : 'transmissions'} live</span>
                  </div>
                  {watching.length > 0 && (
                    <ul className="hero-viewers">
                      {watching.slice(0, 5).map((w) => (
                        <li key={streamKey(w)}>
                          <button type="button" className="hero-viewer" onClick={() => open('stream', streamKey(w))}>
                            <span className="user-name">{w.user}</span>
                            <span className="muted"> · {placeName(w.location)}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              }
            />
          </Suspense>
        </section>
      )}
      <EngineeringTile s={s} />
      {(s.media || j) && <HolonetTile s={s} />}
      <ArchivesTile s={s} />
      <ArmoryTile />
      <FeedTile />
      <RecapTile ledger={s.ledger} />
    </div>
  )
}
