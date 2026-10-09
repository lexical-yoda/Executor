import { ExternalLink, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type ServiceHistory } from '../api'
import { ago, latency } from '../format'
import { useApp } from '../state'
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
function UptimeBars({ history }: { history: ServiceHistory }) {
  const { buckets, bucket_s, hours } = history
  const slots = Math.ceil((hours * 3600) / bucket_s)
  const end = Math.floor(Date.now() / 1000 / bucket_s) * bucket_s
  const byTime = new Map(buckets.map((b) => [b.t, b]))
  const cells = Array.from({ length: slots }, (_, i) => byTime.get(end - (slots - 1 - i) * bucket_s))
  const maxMs = Math.max(1, ...buckets.map((b) => b.ms ?? 0))
  return (
    <div className="uptime">
      <div className="uptime-bars" aria-label="Checks over time, oldest on the left">
        {cells.map((b, i) => {
          const tone = !b ? 'none' : b.down ? 'down' : b.degraded ? 'degraded' : 'up'
          const when = new Date((end - (slots - 1 - i) * bucket_s) * 1000).toLocaleString(undefined, {
            day: 'numeric',
            month: 'short',
            hour: '2-digit',
            minute: '2-digit',
          })
          return (
            <span
              key={i}
              className={`uptime-cell u-${tone}`}
              style={{ ['--h' as string]: b?.ms != null ? `${20 + (b.ms / maxMs) * 80}%` : '100%' }}
              title={
                b
                  ? `${when} · ${b.up} up${b.degraded ? `, ${b.degraded} degraded` : ''}${b.down ? `, ${b.down} down` : ''}${b.ms != null ? ` · ${latency(b.ms)}` : ''}`
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
  const { snapshot, now } = useApp()
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
          Found automatically from the {service.stack ? <span className="mono">{service.stack}</span> : 'Docker'} stack. Its
          name, group, link and check come from Docker; add it to config.yaml, or put executor.name, executor.group or
          executor.url labels in its compose file, to change them.
        </p>
      )}
      {service.error === 'Stack stopped' && (
        <p className="small muted">
          Its containers are gone but its stack folder is still there, so it counts as stopped rather than down. Delete the
          stack and it leaves the page.
        </p>
      )}
      {service.url && (
        <a className="btn btn-ghost btn-small" href={service.url} target="_blank" rel="noopener noreferrer">
          <ExternalLink size={14} /> Open {service.name}
        </a>
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
