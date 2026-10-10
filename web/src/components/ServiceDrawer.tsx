import { ExternalLink, Loader2, Settings2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type ServiceHistory } from '../api'
import { ago, latency } from '../format'
import { useApp, useClock } from '../state'
import { AttachedActions } from './ActionKit'
import { containerTone } from './Services'
import { StatusPill } from './StatusDot'
import { Empty, Facts, SourceNote } from './ui'

const SPANS = [
  { hours: 24, label: '24h' },
  { hours: 168, label: '7d' },
  { hours: 720, label: '30d' },
]

/** Uptime as a strip of bars, one per bucket, coloured by the worst result in it. */
// At most this many bars, so each stays a visible width on a phone.
const MAX_BARS = 96

function UptimeBars({ history }: { history: ServiceHistory }) {
  const { buckets, bucket_s, hours } = history
  const slots = Math.ceil((hours * 3600) / bucket_s)
  const per = Math.max(1, Math.ceil(slots / MAX_BARS))
  const span = bucket_s * per
  const end = Math.floor(Date.now() / 1000 / bucket_s) * bucket_s
  const first = end - (slots - 1) * bucket_s
  // Fold neighbouring buckets into one bar: counts add up, response time averages.
  const bars = Array.from({ length: Math.ceil(slots / per) }, () => ({ up: 0, degraded: 0, down: 0, ms: 0, timed: 0, seen: false }))
  for (const b of buckets) {
    const at = Math.floor((b.t - first) / bucket_s / per)
    const bar = bars[at]
    if (!bar) continue
    bar.seen = true
    bar.up += b.up
    bar.degraded += b.degraded
    bar.down += b.down
    if (b.ms != null) {
      bar.ms += b.ms
      bar.timed += 1
    }
  }
  const avg = (bar: (typeof bars)[number]) => (bar.timed ? bar.ms / bar.timed : null)
  const maxMs = Math.max(1, ...bars.map((bar) => avg(bar) ?? 0))
  return (
    <div className="uptime">
      <div className="uptime-bars" aria-label="Checks over time, oldest on the left">
        {bars.map((bar, i) => {
          const tone = !bar.seen ? 'none' : bar.down ? 'down' : bar.degraded ? 'degraded' : 'up'
          const when = new Date((first + i * span) * 1000).toLocaleString(undefined, {
            day: 'numeric',
            month: 'short',
            hour: '2-digit',
            minute: '2-digit',
          })
          const ms = avg(bar)
          return (
            <span
              key={i}
              className={`uptime-cell u-${tone}`}
              style={{ ['--h' as string]: ms != null ? `${20 + (ms / maxMs) * 80}%` : '100%' }}
              title={
                bar.seen
                  ? `${when} · ${bar.up} up${bar.degraded ? `, ${bar.degraded} degraded` : ''}${bar.down ? `, ${bar.down} down` : ''}${ms != null ? ` · ${latency(ms)}` : ''}`
                  : `${when} · no checks`
              }
            />
          )
        })}
      </div>
      <div className="daily-legend small muted">
        <span>{hours >= 168 ? `${hours / 24} days ago` : `${hours} hours ago`}</span>
        <span>bar height shows response time</span>
        <span>now</span>
      </div>
    </div>
  )
}

export function ServiceDrawer({ id }: { id: string }) {
  const { snapshot, go } = useApp()
  const now = useClock()
  const service = snapshot?.services.find((s) => s.id === id)
  const [hours, setHours] = useState(24)
  const [history, setHistory] = useState<ServiceHistory | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!snapshot?.ledger) return
    let live = true
    setError(null)
    const load = () =>
      api
        .serviceHistory(id, hours)
        .then((h) => live && setHistory(h))
        .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    void load()
    const timer = window.setInterval(load, 60_000)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [id, hours, snapshot?.ledger])

  if (!service) return <Empty>This service is not in the configuration.</Empty>
  return (
    <div>
      <div className="drawer-kicker">
        Service · {service.group} <StatusPill status={service.status} />
      </div>
      <h3 className="drawer-title">{service.name}</h3>
      {service.description && <p className="muted">{service.description}</p>}
      {service.discovered && (
        <p className="small muted discovered-note">
          Found automatically from the {service.stack ? <span className="mono">{service.stack}</span> : 'Docker'} stack.
          Rename it, move it to a group or give it a link in Settings.
        </p>
      )}
      {service.error === 'Stack stopped' && (
        <p className="small muted">
          Its containers are gone but its stack folder is still there, so it counts as stopped rather than down. Delete the
          stack and it leaves the page.
        </p>
      )}
      <div className="drawer-links">
        {service.url && (
          <a className="btn btn-ghost btn-small" href={service.url} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={14} /> Open {service.name}
          </a>
        )}
        {snapshot?.editable && (
          <button
            type="button"
            className="btn btn-ghost btn-small"
            onClick={() => go({ deck: 'settings', drawer: { kind: 'focus', id: service.id } })}
          >
            <Settings2 size={14} /> Name, group or link
          </button>
        )}
      </div>
      {service.edited && service.defaults && (
        <p className="small muted">
          Placed from Settings; the config says {service.defaults.name} in {service.defaults.group}.
        </p>
      )}
      <Facts
        items={[
          ['Check', service.checked_at ? `${service.error ?? 'OK'} · ${ago(service.checked_at, now)}` : 'No check configured'],
          ['Response', service.latency_ms != null ? latency(service.latency_ms) : null],
          ['HTTP', service.http_status != null ? String(service.http_status) : null],
        ]}
      />
      {service.containers.length > 0 && (
        <>
          <h4 className="drawer-sub">Containers</h4>
          <ul className="container-list">
            {service.containers.map((c, i) => (
              <li key={c.name} className={`chip-row chip-${containerTone(c)}`}>
                <span className="chip-dot" />
                <span className="mono">{c.name}</span>
                {i === 0 && <span className="badge-soft">primary</span>}
                <span className="small muted">{c.status}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <AttachedActions target={service.id} />
      {snapshot?.ledger && (
        <>
          <div className="drawer-row">
            <h4 className="drawer-sub">
              Uptime
              {history?.uptime != null && <span className="uptime-pct num"> {history.uptime.toFixed(2)}%</span>}
            </h4>
            <div className="range-tabs" role="tablist">
              {SPANS.map((s) => (
                <button
                  key={s.hours}
                  type="button"
                  role="tab"
                  aria-selected={hours === s.hours}
                  className={hours === s.hours ? 'active' : ''}
                  onClick={() => setHours(s.hours)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          {error && <p className="error small">{error}</p>}
          {!history && !error && <Loader2 size={14} className="spin" />}
          {history && <UptimeBars history={history} />}
          {history && !history.buckets.length && <p className="small muted">No checks recorded yet.</p>}
        </>
      )}
      <SourceNote source={service.discovered ? "Docker's compose labels, the runner's container list and Executor's checks" : "Executor's own checks and the runner's container list"} at={service.checked_at} />
    </div>
  )
}

/** A container reference (from the event log) shows the service that owns it. */
export function ContainerDrawer({ name }: { name: string }) {
  const { snapshot } = useApp()
  const owner = snapshot?.services.find((s) => s.containers.some((c) => c.name === name))
  if (owner) return <ServiceDrawer id={owner.id} />
  return <Empty>Container {name} belongs to no configured service.</Empty>
}
