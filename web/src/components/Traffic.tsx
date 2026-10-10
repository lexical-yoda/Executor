import { Globe2, Loader2, Map as MapIcon, ShieldAlert } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { api, type SiteDetail, type SiteTraffic, type ThreatDetail, type Traffic } from '../api'
import { nodeStatuses } from '../decks/Bridge'
import { bytes } from '../format'
import { useApp } from '../state'
import { Empty, Facts, Num, RowButton, SourceNote } from './ui'
import { RangePicker } from './RangePicker'

const MapView = lazy(() => import('../map/MapView'))

const regions = (() => {
  try {
    return new Intl.DisplayNames(undefined, { type: 'region' })
  } catch {
    return null
  }
})()

export function countryName(code: string | null | undefined): string {
  if (!code) return 'Unknown'
  try {
    return regions?.of(code.toUpperCase()) ?? code
  } catch {
    return code
  }
}

export function ms(value: number | null, slow = false): string {
  if (value == null) return slow ? 'over 10 s' : '—'
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)} s` : `${value} ms`
}

const n = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString())

/** Small columns, one per hour; the last one is the hour in progress. */
function MiniBars({ values, className }: { values: number[]; className: string }) {
  if (!values.length) return null
  const max = Math.max(1, ...values)
  return (
    <svg className={`mini-bars ${className}`} viewBox={`0 0 ${values.length * 4} 20`} preserveAspectRatio="none" aria-hidden="true">
      {values.map((v, i) => {
        const h = v ? Math.max(1.5, (v / max) * 19) : 0.8
        return <rect key={i} x={i * 4} y={20 - h} width={3} height={h} rx={0.6} className={v ? 'on' : 'off'} />
      })}
    </svg>
  )
}

/** Stacked columns for a range: each part a share of the bucket's total. */
function StackedBars({ rows, parts, labels }: { rows: number[][]; parts: string[]; labels: [string, string] }) {
  if (rows.length < 2) return null
  const max = Math.max(1, ...rows.map((r) => r.reduce((a, b) => a + b, 0)))
  const w = 6
  return (
    <div className="query-bars">
      <svg viewBox={`0 0 ${rows.length * w} 40`} preserveAspectRatio="none" aria-hidden="true">
        {rows.map((r, i) => {
          let y = 40
          return (
            <g key={i}>
              {r.map((v, j) => {
                const h = (v / max) * 38
                y -= h
                return <rect key={j} x={i * w} y={y} width={w - 1.2} height={h} className={parts[j]} />
              })}
            </g>
          )
        })}
      </svg>
      <div className="daily-legend small muted num">
        <span>{labels[0]}</span>
        <span>{labels[1]}</span>
      </div>
    </div>
  )
}

const SITE_RANGES = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
]

function SiteRow({ site }: { site: SiteTraffic }) {
  const { open } = useApp()
  const failing = site.recent && site.recent.requests >= 20 && site.recent.s5 / site.recent.requests > 0.05
  return (
    <li>
      <RowButton className="site-row" onClick={() => open('site', site.site)}>
        <span className="site-name mono">{site.site}</span>
        <MiniBars values={site.spark ?? []} className="bars-data" />
        <span className="num site-requests">{n(site.requests)}</span>
        <span className={`small num ${failing || site.errors_pct >= 5 ? 'warn-text' : 'muted'} site-errors`}>
          {site.errors_pct ? `${site.errors_pct}% errors` : 'no errors'}
        </span>
        <span className="small muted num site-latency">{site.p95_ms != null || site.slow ? `p95 ${ms(site.p95_ms, site.slow)}` : ''}</span>
      </RowButton>
    </li>
  )
}

export function TrafficCard({ traffic }: { traffic: Traffic }) {
  const t = traffic.totals
  const sites = (traffic.sites ?? []).filter((s) => s.requests > 0)
  const quiet = (traffic.sites ?? []).length - sites.length
  return (
    <article className="edge-card card traffic-card">
      <div className="card-head">
        <Globe2 size={16} />
        <h3>Public sites</h3>
        <span className="small muted">last 24 h</span>
      </div>
      {traffic.error && <p className="small warn-text">Traffic unavailable: {traffic.error}</p>}
      {!traffic.ok && !traffic.error && <Empty>Waiting for the edge server's first summary…</Empty>}
      {traffic.ok && t && (
        <>
          <div className="backup-main">
            <Num className="backup-big" value={t.requests} format={(v) => Math.round(v).toLocaleString()} />
            <span className="small muted">
              requests{t.own ? ` · ${Math.round((t.own / Math.max(1, t.requests)) * 100)}% from home` : ''} · {bytes(t.bytes)} served
            </span>
          </div>
          <ul className="site-rows">
            {sites.map((s) => (
              <SiteRow key={s.site} site={s} />
            ))}
          </ul>
          <span className="small muted">
            {(traffic.countries ?? [])
              .slice(0, 5)
              .map((c) => `${countryName(c.country_code)} ${n(c.n)}`)
              .join(' · ') || 'No visitor locations yet'}
            {quiet > 0 && ` · ${quiet} more ${quiet === 1 ? 'site' : 'sites'} with only health checks`}
          </span>
        </>
      )}
    </article>
  )
}

export function ShieldsCard({ traffic }: { traffic: Traffic }) {
  const { open } = useApp()
  const th = traffic.threats
  if (!traffic.ok || !th) return null
  const total = th.ssh + th.fw + th.scans
  return (
    <article
      className="edge-card card clickable shields-card"
      onClick={() => open('threats')}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), open('threats'))}
      role="button"
      tabIndex={0}
    >
      <div className="card-head">
        <ShieldAlert size={16} />
        <h3>Shields</h3>
        <span className="small muted">last 24 h</span>
      </div>
      <div className="backup-main">
        <Num className="backup-big" value={total} format={(v) => Math.round(v).toLocaleString()} />
        <span className="small muted">attempts turned away from {n(th.attackers)} addresses</span>
      </div>
      <MiniBars values={th.spark} className="bars-threat" />
      <div className="stat-chips">
        <span className="stat-chip">
          <span className="num">{n(th.ssh)}</span> SSH logins refused
        </span>
        <span className="stat-chip">
          <span className="num">{n(th.bans)}</span> bans{th.banned_now != null ? ` · ${th.banned_now} now` : ''}
        </span>
        <span className="stat-chip">
          <span className="num">{n(th.scans)}</span> scans
        </span>
        {th.fw > 0 && (
          <span className="stat-chip">
            <span className="num">{n(th.fw)}</span> firewall blocks
          </span>
        )}
      </div>
      {th.countries.length > 0 && (
        <span className="small muted">From {th.countries.slice(0, 4).map((c) => countryName(c.country_code)).join(', ')}</span>
      )}
      {th.users.length > 0 && (
        <span className="small muted">
          Tried logging in as <span className="mono">{th.users.slice(0, 5).map((u) => u.tag).join(', ')}</span>
        </span>
      )}
    </article>
  )
}

function useDetail<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setData(null)
    load()
      .then((d) => live && (setData(d), setError(null)))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, error }
}

function rangeLabels(range: string): [string, string] {
  return [range === '24h' ? '24 h ago' : range === '7d' ? '7 days ago' : '30 days ago', 'now']
}

export function SiteDrawer({ site }: { site: string }) {
  const { snapshot } = useApp()
  const [range, setRange] = useState('24h')
  const { data, error } = useDetail<SiteDetail>(() => api.edgeSite(site, range), [site, range])
  const live = snapshot?.edge?.traffic?.sites?.find((s) => s.site === site)
  const t = data?.totals
  return (
    <div>
      <div className="drawer-kicker">
        <Globe2 size={14} /> Public site
      </div>
      <div className="drawer-row">
        <h3 className="drawer-title mono">{site}</h3>
        <RangePicker value={range} options={SITE_RANGES} onChange={setRange} label="Range" />
      </div>
      {error && <p className="small warn-text">{error}</p>}
      {!data && !error && <Loader2 size={14} className="spin" />}
      {data && (
        <>
          <StackedBars
            rows={data.series.map((p) => [Math.max(0, p.requests - p.s4 - p.s5), p.s4, p.s5])}
            parts={['sb-ok', 'sb-4xx', 'sb-5xx']}
            labels={rangeLabels(range)}
          />
          <p className="small muted legend-line">
            <i className="sb-ok-key" /> answered <i className="sb-4xx-key" /> client errors <i className="sb-5xx-key" /> server errors
          </p>
          {t ? (
            <Facts
              items={[
                ['Requests', `${n(t.requests)}${t.own ? ` (${n(t.own)} from home)` : ''}`],
                ['Health checks', `${n(t.monitor)}, not counted above`],
                ['Server errors', `${n(t.s5)} (${t.errors_pct}%)`],
                ['Client errors', n(t.s4)],
                ['Response time', t.p50_ms != null ? `half within ${ms(t.p50_ms)}, 95% within ${ms(t.p95_ms, t.slow)}` : null],
                ['Edge cache hits', t.cache_pct != null ? `${t.cache_pct}%` : null],
                ['Data served', bytes(t.bytes)],
                ['Last 15 minutes', live?.recent ? `${n(live.recent.requests)} requests · ${n(live.recent.s5)} server errors` : null],
              ]}
            />
          ) : (
            <Empty>No requests in this range.</Empty>
          )}
          <h4 className="drawer-sub">Where visitors are</h4>
          {data.places.length ? (
            <ol className="recap-list">
              {data.places.map((p) => (
                <li key={`${p.country_code}-${p.city}`}>
                  <span>{p.city ? `${p.city}, ${p.country_code}` : p.country_code ? countryName(p.country_code) : 'Unknown place'}</span>
                  <span className="small muted num">{n(p.n)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <Empty>No visitor places in this range.</Empty>
          )}
          <p className="small muted">Places are approximate (city level); visitor addresses are not kept.</p>
        </>
      )}
      <SourceNote source="The edge server's nginx log, summarised every 5 minutes" at={snapshot?.edge?.traffic?.generated ?? null} />
    </div>
  )
}

export function ThreatsDrawer() {
  const { snapshot, open } = useApp()
  const [range, setRange] = useState('24h')
  const { data, error } = useDetail<ThreatDetail>(() => api.edgeThreats(range), [range])
  const statuses = useMemo(() => nodeStatuses(snapshot), [snapshot])
  const hub = snapshot?.jellyfin?.hub ?? null
  const total = data ? data.totals.ssh + data.totals.fw + data.totals.scans : null
  return (
    <div className="threats-drawer">
      <div className="drawer-kicker">
        <ShieldAlert size={14} /> Shields
      </div>
      <div className="drawer-row">
        <h3 className="drawer-title">Attacks on the edge server</h3>
        <RangePicker value={range} options={SITE_RANGES.slice(0, 2)} onChange={setRange} label="Range" />
      </div>
      {error && <p className="small warn-text">{error}</p>}
      {!data && !error && <Loader2 size={14} className="spin" />}
      {data && (
        <>
          <div className="threat-map">
            <Suspense
              fallback={
                <div className="map-loading">
                  <MapIcon size={22} className="spin-slow" />
                </div>
              }
            >
              <MapView
                variant="full"
                mode="threats"
                origin={null}
                hub={hub}
                nodeStatus={statuses}
                live={[]}
                places={[]}
                trail={[]}
                replay={null}
                selected={null}
                threats={data.points}
                onOpen={(kind, id) => kind === 'machine' && open('machine', id)}
              />
            </Suspense>
          </div>
          <div className="recap-stats">
            <div className="recap-stat">
              <Num value={total} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
              <span className="small muted">attempts</span>
            </div>
            <div className="recap-stat">
              <Num value={data.totals.ssh} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
              <span className="small muted">SSH logins refused</span>
            </div>
            <div className="recap-stat">
              <Num value={data.totals.bans} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
              <span className="small muted">addresses banned</span>
            </div>
          </div>
          <StackedBars
            rows={data.series.map((p) => [p.ssh, p.scans, p.fw])}
            parts={['sb-ssh', 'sb-scan', 'sb-fw']}
            labels={rangeLabels(range)}
          />
          <p className="small muted legend-line">
            <i className="sb-ssh-key" /> SSH logins <i className="sb-scan-key" /> scans of the web server <i className="sb-fw-key" /> firewall
          </p>
          <h4 className="drawer-sub">Busiest sources</h4>
          <ol className="recap-list threat-sources">
            {data.sources.map((s) => (
              <li key={s.ip}>
                <span className="mono">{s.ip}</span>
                <span className="small muted num">
                  {[s.city, s.country_code].filter(Boolean).join(', ') || 'unknown place'} · {n(s.ssh + s.fw + s.scans)}
                  {s.banned ? <span className="warn-text"> · banned</span> : null}
                </span>
              </li>
            ))}
          </ol>
          {data.users.length > 0 && (
            <>
              <h4 className="drawer-sub">Usernames tried</h4>
              <p className="small mono tag-cloud">
                {data.users.map((u) => `${u.tag} ${u.n}`).join(' · ')}
              </p>
            </>
          )}
          {data.ports.length > 0 && (
            <>
              <h4 className="drawer-sub">Ports probed</h4>
              <p className="small mono tag-cloud">{data.ports.map((p) => `${p.tag} ${p.n}`).join(' · ')}</p>
            </>
          )}
          <p className="small muted">
            Counted from the SSH log, fail2ban, the firewall and requests to host names the server does not serve.
            Addresses are kept for a week.
          </p>
        </>
      )}
      <SourceNote source="The edge server's logs, summarised every 5 minutes" at={snapshot?.edge?.traffic?.generated ?? null} />
    </div>
  )
}
