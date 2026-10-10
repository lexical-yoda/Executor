import { ShieldBan, ShieldOff } from 'lucide-react'
import type { KeyboardEvent } from 'react'
import type { PiHoleStatus } from '../api'
import { ago, duration } from '../format'
import { useApp, useClock } from '../state'
import { DeckSection, Empty, Facts, Num, SourceNote } from './ui'
import { Badge } from './Badge'
import { AttachedActions } from './ActionKit'

const n = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString())

/** The last day of queries in ten-minute slots: allowed below, blocked on top. */
function QueryBars({ history }: { history: { t: number; total: number; blocked: number }[] }) {
  if (history.length < 2) return null
  const max = Math.max(1, ...history.map((h) => h.total))
  const w = 3
  return (
    <div className="query-bars">
      <svg viewBox={`0 0 ${history.length * w} 40`} preserveAspectRatio="none" aria-hidden="true">
        {history.map((h, i) => {
          const total = (h.total / max) * 38
          const blocked = (h.blocked / max) * 38
          return (
            <g key={h.t}>
              <rect x={i * w} y={40 - total} width={w - 0.6} height={Math.max(0, total - blocked)} className="qb-allowed" />
              <rect x={i * w} y={40 - blocked} width={w - 0.6} height={blocked} className="qb-blocked" />
            </g>
          )
        })}
      </svg>
      <div className="daily-legend small muted num">
        <span>24 h ago</span>
        <span>
          <i className="qb-allowed-key" />
          allowed <i className="qb-blocked-key" />
          blocked
        </span>
        <span>now</span>
      </div>
    </div>
  )
}

function BlockingPill({ dns }: { dns: PiHoleStatus }) {
  if (!dns.blocking) return null
  const on = dns.blocking === 'enabled'
  return (
    <Badge tone={on ? 'good' : 'warn'}>
      {on ? 'Blocking' : dns.blocking_timer ? `Off for ${duration(dns.blocking_timer)}` : 'Not blocking'}
    </Badge>
  )
}

export function DnsCard({ dns }: { dns: PiHoleStatus }) {
  const { open } = useApp()
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      open('dns')
    }
  }
  return (
    <DeckSection id="dns" title="DNS">
      {!dns.configured && <p className="small warn-text">Pi-hole is configured but PIHOLE_PASSWORD is not set.</p>}
      {dns.configured && dns.error && <p className="small warn-text">Pi-hole unavailable: {dns.error}</p>}
      {dns.configured && !dns.ok && !dns.error && <Empty>Asking Pi-hole…</Empty>}
      {dns.ok && (
        <article
          className={`backup-card card clickable dns-card bk-edge-${dns.blocking === 'enabled' ? 'ok' : 'warning'}`}
          onClick={() => open('dns')}
          onKeyDown={onKey}
          role="button"
          tabIndex={0}
        >
          <div className="card-head">
            {dns.blocking === 'enabled' ? <ShieldBan size={16} /> : <ShieldOff size={16} />}
            <h3>Pi-hole</h3>
            <BlockingPill dns={dns} />
          </div>
          <div className="dns-body">
            <div className="dns-main">
              <div className="backup-main">
                <Num className="backup-big" value={dns.blocked ?? 0} format={(v) => Math.round(v).toLocaleString()} />
                <span className="small muted">
                  blocked of {n(dns.queries)} queries today ({dns.pct_blocked ?? 0}%)
                </span>
              </div>
              <div className="stat-chips">
                <span className="stat-chip">
                  <span className="num">{n(dns.clients_active)}</span> active clients
                </span>
                {!!dns.queries && (
                  <span className="stat-chip">
                    <span className="num">{Math.round(((dns.cached ?? 0) / dns.queries) * 100)}%</span> answered from cache
                  </span>
                )}
                <span className="stat-chip">
                  <span className="num">{n(dns.blocklist_domains)}</span> domains on blocklists
                </span>
              </div>
              {(dns.top_blocked ?? []).length > 0 && (
                <ol className="dns-top">
                  {dns.top_blocked!.slice(0, 4).map((d) => (
                    <li key={d.domain}>
                      <span className="mono">{d.domain}</span>
                      <span className="small muted num">{n(d.count)}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
            <QueryBars history={dns.history ?? []} />
          </div>
          <AttachedActions target="dns" />
        </article>
      )}
    </DeckSection>
  )
}

export function DnsDrawer() {
  const { snapshot } = useApp()
  const now = useClock()
  const dns = snapshot?.pihole
  if (!dns) return <Empty>Pi-hole is not configured.</Empty>
  return (
    <div>
      <div className="drawer-kicker">
        <ShieldBan size={14} /> Pi-hole <BlockingPill dns={dns} />
      </div>
      <h3 className="drawer-title">DNS today</h3>
      {dns.error && <p className="small warn-text">{dns.error}</p>}
      {dns.ok && (
        <>
          <div className="recap-stats">
            <div className="recap-stat">
              <Num value={dns.queries ?? 0} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
              <span className="small muted">queries</span>
            </div>
            <div className="recap-stat">
              <Num value={dns.blocked ?? 0} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
              <span className="small muted">blocked</span>
            </div>
            <div className="recap-stat">
              <Num value={dns.pct_blocked ?? 0} format={(v) => `${v.toFixed(1)}%`} className="recap-big" />
              <span className="small muted">blocked share</span>
            </div>
          </div>
          <QueryBars history={dns.history ?? []} />
          <Facts
            items={[
              ['Answered from cache', n(dns.cached)],
              ['Forwarded upstream', n(dns.forwarded)],
              ['Different domains', n(dns.unique_domains)],
              ['Active clients', n(dns.clients_active)],
              ['Blocklists', dns.blocklist_domains != null ? `${n(dns.blocklist_domains)} domains${dns.blocklist_updated ? `, updated ${ago(dns.blocklist_updated * 1000, now)}` : ''}` : null],
              ['Version', dns.version ?? null],
            ]}
          />
          <h4 className="drawer-sub">Most blocked</h4>
          <ol className="recap-list">
            {(dns.top_blocked ?? []).map((d) => (
              <li key={d.domain}>
                <span className="mono">{d.domain}</span>
                <span className="small muted num">{n(d.count)}</span>
              </li>
            ))}
          </ol>
          <h4 className="drawer-sub">Busiest clients</h4>
          <ol className="recap-list">
            {(dns.top_clients ?? []).map((c) => (
              <li key={c.ip}>
                <span className="user-name">{c.name}</span>
                <span className="small muted num">{n(c.count)}</span>
              </li>
            ))}
          </ol>
        </>
      )}
      <AttachedActions target="dns" />
      <SourceNote source="Pi-hole API (app password)" at={dns.checked_at} />
    </div>
  )
}
