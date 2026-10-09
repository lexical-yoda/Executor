import { Briefcase, Download, ExternalLink, Film, Globe, Image, Layers, Network, Search, Wrench } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ContainerState, ServiceStatus } from '../api'
import { latency } from '../format'
import { StatusDot } from './StatusDot'

const GROUP_ICONS: Record<string, typeof Film> = {
  Media: Film,
  Downloads: Download,
  'Photos & Files': Image,
  Productivity: Briefcase,
  Tools: Wrench,
  'Network & Infra': Network,
  'Public edge': Globe,
}

function containerTone(c: ContainerState) {
  if (c.state !== 'running') return 'down'
  if (c.health === 'unhealthy') return 'down'
  if (c.health === 'starting') return 'degraded'
  return 'up'
}

function hue(name: string) {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}

function Tile({ service, index }: { service: ServiceStatus; index: number }) {
  const body = (
    <>
      <div className="tile-top">
        <span className="monogram" style={{ ['--h' as string]: hue(service.name) }}>
          {service.name.slice(0, 2)}
        </span>
        <div className="tile-title">
          <h3>{service.name}</h3>
          <span className="small muted">
            {service.status === 'down' && service.error
              ? service.error
              : (service.description ?? latency(service.latency_ms))}
          </span>
        </div>
        <StatusDot status={service.status} />
      </div>
      {service.containers.length > 0 && (
        <ul className="chips">
          {service.containers.map((c) => (
            <li key={c.name} className={`chip chip-${containerTone(c)}`} title={`${c.name}: ${c.status}`}>
              <span className="chip-dot" />
              {c.name}
            </li>
          ))}
        </ul>
      )}
      {service.url && <ExternalLink className="tile-link" size={14} aria-hidden="true" />}
    </>
  )
  const className = `tile card status-${service.status}`
  const style = { ['--i' as string]: index }
  return service.url ? (
    <a className={className} style={style} href={service.url} target="_blank" rel="noopener noreferrer">
      {body}
    </a>
  ) : (
    <div className={className} style={style}>
      {body}
    </div>
  )
}

export function Services({ services, groups }: { services: ServiceStatus[]; groups: string[] }) {
  const [query, setQuery] = useState('')
  const [issuesOnly, setIssuesOnly] = useState(false)

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return services.filter(
      (s) =>
        (!issuesOnly || s.status !== 'up') &&
        (!q || s.name.toLowerCase().includes(q) || s.containers.some((c) => c.name.toLowerCase().includes(q))),
    )
  }, [services, query, issuesOnly])

  const issues = services.filter((s) => s.status !== 'up').length

  return (
    <section className="section">
      <div className="section-head">
        <h2>Services</h2>
        <div className="toolbar">
          <label className="search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              placeholder="Filter services or containers"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Filter services"
            />
          </label>
          <button
            type="button"
            className={`toggle ${issuesOnly ? 'toggle-on' : ''}`}
            onClick={() => setIssuesOnly((v) => !v)}
            aria-pressed={issuesOnly}
          >
            Issues only{issues ? ` (${issues})` : ''}
          </button>
        </div>
      </div>

      {groups.map((group) => {
        const items = visible.filter((s) => s.group === group)
        if (!items.length) return null
        const Icon = GROUP_ICONS[group] ?? Layers
        const up = items.filter((s) => s.status === 'up').length
        return (
          <div key={group} className="group">
            <div className="group-head">
              <Icon size={15} strokeWidth={1.8} aria-hidden="true" />
              <h3>{group}</h3>
              <span className="small muted num">
                {up}/{items.length}
              </span>
            </div>
            <div className="tiles">
              {items.map((s, i) => (
                <Tile key={s.id} service={s} index={i} />
              ))}
            </div>
          </div>
        )
      })}
      {!visible.length && <p className="empty muted">Nothing matches.</p>}
    </section>
  )
}
