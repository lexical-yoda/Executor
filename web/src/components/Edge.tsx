import { Globe, ShieldCheck } from 'lucide-react'
import type { Bandwidth, Certificate, Edge as EdgeData } from '../api'
import { ago } from '../format'
import { useApp, useClock } from '../state'
import { ShieldsCard, TrafficCard } from './Traffic'
import { AttachedActions } from './ActionKit'
import { Empty, Facts, SourceNote } from './ui'

export function gb(value: number | null): string {
  if (value === null) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} TB` : `${Math.round(value)} GB`
}

function tone(pct: number | null) {
  if (pct === null) return 'unknown'
  return pct >= 90 ? 'down' : pct >= 70 ? 'degraded' : 'up'
}

export function DailyBars({ daily, monthStart }: { daily: Bandwidth['daily']; monthStart: number | null }) {
  if (!monthStart) return null
  // One slot per day of the current month, so the chart fills in as the month goes.
  const first = new Date(monthStart * 1000)
  const year = first.getUTCFullYear()
  const month = first.getUTCMonth()
  const length = new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
  const prefix = `${year}-${String(month + 1).padStart(2, '0')}-`
  const byDay = new Map(daily.filter((d) => d.date.startsWith(prefix)).map((d) => [Number(d.date.slice(8)), d]))
  const max = Math.max(1, ...[...byDay.values()].map((d) => Math.max(d.out_gb, d.in_gb)))
  const w = 100 / length
  const today = new Date().getUTCDate()
  const monthName = first.toLocaleString(undefined, { month: 'long', timeZone: 'UTC' })
  return (
    <div className="daily">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="daily-svg" aria-hidden="true">
        {Array.from({ length }, (_, i) => {
          const d = byDay.get(i + 1)
          return (
            <g key={i}>
              {i + 1 === today && <rect className="day-today" x={i * w} width={w} y={0} height={40} />}
              {d && (
                <>
                  <rect
                    className="bar-in"
                    x={i * w + w * 0.12}
                    width={w * 0.36}
                    y={40 - (d.in_gb / max) * 38}
                    height={(d.in_gb / max) * 38}
                  />
                  <rect
                    className="bar-out"
                    x={i * w + w * 0.52}
                    width={w * 0.36}
                    y={40 - (d.out_gb / max) * 38}
                    height={(d.out_gb / max) * 38}
                  />
                </>
              )}
              {!d && i + 1 < today && (
                <rect className="day-empty" x={i * w + w * 0.3} width={w * 0.4} y={38.5} height={1.5} />
              )}
            </g>
          )
        })}
      </svg>
      <div className="daily-legend small muted">
        <span>
          <i className="lg-out" /> out
        </span>
        <span>
          <i className="lg-in" /> in (free)
        </span>
        <span className="daily-range num">
          {monthName} 1–{length} · peak {gb(max)}/day
        </span>
      </div>
    </div>
  )
}

function BandwidthCard({ bw, error, now }: { bw: Bandwidth | null; error: string | null; now: number }) {
  const { open } = useApp()
  if (!bw) {
    return (
      <article className="edge-card card">
        <div className="edge-head">
          <Globe size={16} /> <h3>VPS bandwidth</h3>
        </div>
        <p className="small muted">{error ? `Unavailable: ${error}` : 'Waiting for data…'}</p>
      </article>
    )
  }
  const used = bw.used_pct
  const projected = bw.projected_pct
  const allocatedPct =
    bw.allowance_now_gb !== null && bw.allowance_month_gb ? (bw.allowance_now_gb / bw.allowance_month_gb) * 100 : null
  const fetchedMs = bw.fetched_at ? bw.fetched_at * 1000 : null
  return (
    <article className="edge-card card clickable" onClick={() => open('bandwidth', '')} role="button" tabIndex={0}>
      <div className="edge-head">
        <Globe size={16} />
        <h3>VPS bandwidth</h3>
        <span className="small muted">{bw.instance?.label ?? ''}</span>
      </div>

      <div className="bw-figures">
        <div>
          <span className="bw-big num">{gb(bw.out_gb)}</span>
          <span className="small muted"> out this month</span>
        </div>
        <div className={`bw-proj tone-${tone(projected)}`}>
          <span className="num">~{gb(bw.projected_out_gb)}</span>
          <span className="small muted"> projected</span>
        </div>
      </div>

      <div className="bw-bar" title="Outbound this month, against the month's allowance">
        <div className={`bw-fill bar-${tone(used)}`} style={{ width: `${Math.min(100, used ?? 0)}%` }} />
        {projected !== null && (
          <div className="bw-projected" style={{ left: `${Math.min(100, projected)}%` }} title="Projected month end" />
        )}
        {allocatedPct !== null && (
          <div
            className="bw-allocated"
            style={{ left: `${Math.min(100, allocatedPct)}%` }}
            title="Allowance accrued so far"
          />
        )}
      </div>
      <div className="bw-scale small muted num">
        <span>{used !== null ? `${used.toFixed(1)}% used` : ''}</span>
        <span>
          {bw.elapsed_pct !== null ? `${Math.round(bw.elapsed_pct)}% of month gone` : ''} · allowance{' '}
          {gb(bw.allowance_month_gb)}
        </span>
      </div>

      <DailyBars daily={bw.daily} monthStart={bw.month_start} />

      <div className="stat-chips">
        <span className="stat-chip">
          In <span className="num">{gb(bw.in_gb)}</span> (free)
        </span>
        <span className="stat-chip">
          Overage <span className="num">{bw.overage_cost ? `$${bw.overage_cost}` : 'none'}</span>
        </span>
        {bw.previous && (
          <span className="stat-chip">
            Last month <span className="num">{gb(bw.previous.out_gb)}</span>
          </span>
        )}
        {fetchedMs && <span className="stat-chip">Vultr data {ago(fetchedMs, now)}</span>}
      </div>
      {Object.keys(bw.errors).length > 0 && (
        <p className="small warn-text">
          Partial data:{' '}
          {Object.entries(bw.errors)
            .map(([k, v]) => `${k} ${v}`)
            .join(', ')}
        </p>
      )}
    </article>
  )
}

function CertificateCard({ certs }: { certs: Certificate[] }) {
  const { open } = useApp()
  return (
    <article className="edge-card card">
      <div className="edge-head">
        <ShieldCheck size={16} />
        <h3>TLS certificates</h3>
      </div>
      {!certs.length && <p className="small muted">Checking…</p>}
      {certs.map((c) => {
        const t = c.days_left === null ? 'down' : c.days_left < 7 ? 'down' : c.days_left < 20 ? 'degraded' : 'up'
        return (
          <button type="button" key={c.host} className="cert row-btn" onClick={() => open('certificate', c.host)}>
            <div className={`cert-days tone-${t}`}>
              <span className="num">{c.days_left === null ? '—' : Math.floor(c.days_left)}</span>
              <span className="small">days</span>
            </div>
            <div className="cert-text">
              <span className="mono">{c.host}</span>
              <span className="small muted">
                {c.error
                  ? `Check failed: ${c.error}`
                  : `Expires ${new Date(c.expires!).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })} · ${c.issuer ?? ''}`}
              </span>
            </div>
          </button>
        )
      })}
    </article>
  )
}

export function Edge({ edge }: { edge: EdgeData }) {
  const now = useClock()
  return (
    <section className="section">
      <div className="section-head">
        <h2>Edge</h2>
      </div>
      <div className="edge-grid">
        <BandwidthCard bw={edge.bandwidth} error={edge.bandwidth_error} now={now} />
        <CertificateCard certs={edge.certificates} />
      </div>
      {edge.traffic && (
        <div className="edge-grid edge-grid-traffic">
          <TrafficCard traffic={edge.traffic} />
          <ShieldsCard traffic={edge.traffic} />
        </div>
      )}
      <AttachedActions target="edge" />
    </section>
  )
}

export function BandwidthDrawer() {
  const { snapshot } = useApp()
  const bw = snapshot?.edge?.bandwidth
  if (!bw) return <Empty>{snapshot?.edge?.bandwidth_error ?? 'No bandwidth figures yet.'}</Empty>
  return (
    <div>
      <div className="drawer-kicker">Edge · {bw.instance?.label ?? 'VPS'}</div>
      <h3 className="drawer-title">VPS bandwidth</h3>
      <DailyBars daily={bw.daily} monthStart={bw.month_start} />
      <Facts
        items={[
          ['Out this month', gb(bw.out_gb)],
          ['In this month', `${gb(bw.in_gb)} (free)`],
          ['Projected', bw.projected_out_gb != null ? `${gb(bw.projected_out_gb)} (${Math.round(bw.projected_pct ?? 0)}%)` : null],
          ['Allowance', `${gb(bw.allowance_now_gb)} accrued of ${gb(bw.allowance_month_gb)}`],
          ['Month elapsed', bw.elapsed_pct != null ? `${Math.round(bw.elapsed_pct)}%` : null],
          ['Overage', bw.overage_cost ? `$${bw.overage_cost} (${gb(bw.overage_gb)})` : 'none'],
          ['Last month', bw.previous ? `${gb(bw.previous.out_gb)} out · ${gb(bw.previous.in_gb)} in` : null],
          ['Plan', [bw.instance?.plan, bw.instance?.region].filter(Boolean).join(' · ') || null],
        ]}
      />
      {Object.keys(bw.errors).length > 0 && (
        <p className="small warn-text">
          Partial data: {Object.entries(bw.errors).map(([k, v]) => `${k} ${v}`).join(', ')}
        </p>
      )}
      <AttachedActions target="edge" />
      <SourceNote source="Vultr API, fetched by the VPS" at={bw.fetched_at} />
    </div>
  )
}

export function CertificateDrawer({ host }: { host: string }) {
  const { snapshot } = useApp()
  const cert = snapshot?.edge?.certificates.find((c) => c.host === host)
  if (!cert) return <Empty>No certificate check for {host}.</Empty>
  return (
    <div>
      <div className="drawer-kicker">Edge · TLS certificate</div>
      <h3 className="drawer-title mono">{cert.host}</h3>
      <Facts
        items={[
          ['Days left', cert.days_left != null ? String(Math.floor(cert.days_left)) : null],
          ['Expires', cert.expires ? new Date(cert.expires).toLocaleString() : null],
          ['Issuer', cert.issuer],
          ['Problem', cert.error],
        ]}
      />
      <p className="small muted">Checked hourly by connecting to the host, as a visitor would.</p>
      <SourceNote source="TLS handshake from the NAS" />
    </div>
  )
}
