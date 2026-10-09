import { ArrowDownRight, ArrowUpRight, CalendarDays, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type Recap as RecapData } from '../api'
import { bytes } from '../format'
import { useApp } from '../state'
import { signed } from './Backups'
import { Empty, Num, RowButton, SourceNote } from './ui'

export function useRecap(days = 7) {
  const [data, setData] = useState<RecapData | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    const load = () =>
      api
        .recap(days)
        .then((r) => live && (setData(r), setError(null)))
        .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    void load()
    const id = window.setInterval(() => !document.hidden && void load(), 300_000)
    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [days])
  return { data, error }
}

function Delta({ now, before, unit = '' }: { now: number; before: number; unit?: string }) {
  if (!before && !now) return null
  if (!before) return <span className="delta delta-up">new</span>
  const change = ((now - before) / before) * 100
  if (Math.abs(change) < 1) return <span className="delta">same</span>
  const up = change > 0
  return (
    <span className={`delta ${up ? 'delta-up' : 'delta-down'}`} title={`Previous period: ${before}${unit}`}>
      {up ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
      {Math.abs(Math.round(change))}%
    </span>
  )
}

function hourName(hour: number | null): string | null {
  if (hour == null) return null
  const d = new Date()
  d.setHours(hour, 0, 0, 0)
  return d.toLocaleTimeString(undefined, { hour: 'numeric' })
}

/** The headline numbers of the week, for the bridge tile. */
export function RecapStats({ data }: { data: RecapData }) {
  const m = data.media
  return (
    <div className="recap-stats">
      <div className="recap-stat">
        <Num value={m.hours} format={(v) => v.toFixed(1)} className="recap-big" />
        <span className="small muted">hours streamed</span>
        <Delta now={m.hours} before={data.previous.hours} />
      </div>
      <div className="recap-stat">
        <Num value={m.plays} className="recap-big" />
        <span className="small muted">plays</span>
        <Delta now={m.plays} before={data.previous.plays} />
      </div>
      <div className="recap-stat">
        <Num value={m.viewers} className="recap-big" />
        <span className="small muted">viewers</span>
        <Delta now={m.viewers} before={data.previous.viewers} />
      </div>
      <div className="recap-stat">
        <Num value={data.downloads.bytes} format={(v) => bytes(v)} className="recap-big" />
        <span className="small muted">downloaded</span>
        <Delta now={data.downloads.bytes} before={data.downloads.bytes_before} />
      </div>
      <div className="recap-stat">
        <Num value={data.reliability.uptime} format={(v) => `${v.toFixed(2)}%`} className="recap-big" />
        <span className="small muted">service uptime</span>
      </div>
      <div className="recap-stat">
        <span className="recap-big num">{data.backups.glacier_growth != null ? signed(data.backups.glacier_growth) : '—'}</span>
        <span className="small muted">Glacier growth</span>
      </div>
    </div>
  )
}

export function RecapHighlights({ data }: { data: RecapData }) {
  const m = data.media
  const top = m.top_titles[0]
  const place = m.top_places[0]
  const items = [
    top && { label: 'Most watched', value: top.title, sub: `${top.hours} h · ${top.viewers} ${top.viewers === 1 ? 'viewer' : 'viewers'}` },
    place && { label: 'Busiest city', value: [place.city, place.country_code].filter(Boolean).join(', '), sub: `${place.plays} plays` },
    m.prime_hour != null && { label: 'Prime time', value: hourName(m.prime_hour)!, sub: 'most plays start' },
    m.countries > 0 && { label: 'Countries', value: String(m.countries), sub: 'tuned in' },
  ].filter(Boolean) as { label: string; value: string; sub: string }[]
  return (
    <ul className="recap-highlights">
      {items.map((i) => (
        <li key={i.label}>
          <span className="small muted">{i.label}</span>
          <span className="recap-hl">{i.value}</span>
          <span className="small muted">{i.sub}</span>
        </li>
      ))}
    </ul>
  )
}

export function RecapDrawer() {
  const { open } = useApp()
  const [days, setDays] = useState(7)
  const { data, error } = useRecap(days)
  return (
    <div className="recap-drawer">
      <div className="drawer-kicker">
        <CalendarDays size={14} /> Recap
      </div>
      <div className="drawer-row">
        <h3 className="drawer-title">The last {days === 7 ? 'week' : `${days} days`}</h3>
        <div className="range-tabs" role="tablist">
          {[7, 30].map((d) => (
            <button key={d} type="button" role="tab" aria-selected={days === d} className={days === d ? 'active' : ''} onClick={() => setDays(d)}>
              {d}d
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error small">{error}</p>}
      {!data && !error && <Loader2 size={14} className="spin" />}
      {data && (
        <>
          <RecapStats data={data} />
          <RecapHighlights data={data} />
          <h4 className="drawer-sub">Top titles</h4>
          {data.media.top_titles.length ? (
            <ol className="recap-list">
              {data.media.top_titles.map((t) => (
                <li key={t.title}>
                  <span>{t.title}</span>
                  <span className="small muted num">
                    {t.hours} h · {t.plays} plays · {t.viewers} {t.viewers === 1 ? 'viewer' : 'viewers'}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <Empty>No plays recorded yet.</Empty>
          )}
          <h4 className="drawer-sub">Top viewers</h4>
          <ol className="recap-list">
            {data.media.top_viewers.map((v) => (
              <li key={v.id}>
                <RowButton onClick={() => open('user', v.id, 'holonet')}>
                  <span className="user-name">{v.name}</span>
                  <span className="small muted num">
                    {v.hours} h · {v.plays} plays
                  </span>
                </RowButton>
              </li>
            ))}
          </ol>
          <h4 className="drawer-sub">Top cities</h4>
          <ol className="recap-list">
            {data.media.top_places.map((p) => (
              <li key={`${p.city}-${p.country_code}`}>
                <span>{[p.city, p.country_code].filter(Boolean).join(', ')}</span>
                <span className="small muted num">
                  {p.plays} plays · {p.viewers} {p.viewers === 1 ? 'viewer' : 'viewers'}
                </span>
              </li>
            ))}
          </ol>
          {data.media.longest && (
            <p className="small muted">
              Longest single play: <span className="user-name">{data.media.longest.name}</span> watched{' '}
              {data.media.longest.title}
              {data.media.longest.episode ? ` (${data.media.longest.episode})` : ''} for {data.media.longest.hours} h.
            </p>
          )}
          <h4 className="drawer-sub">The ship</h4>
          <ul className="recap-list">
            <li>
              <span>Incidents</span>
              <span className="small muted num">
                {data.reliability.incidents} service outages · {data.reliability.machine_outages} machine outages ·{' '}
                {data.reliability.restarts} container restarts
              </span>
            </li>
            {data.reliability.worst && data.reliability.worst.uptime < 100 && (
              <li>
                <RowButton onClick={() => open('service', data.reliability.worst!.service)}>
                  <span>Least reliable</span>
                  <span className="small muted num">
                    {data.reliability.worst.service} · {data.reliability.worst.uptime.toFixed(2)}%
                  </span>
                </RowButton>
              </li>
            )}
            <li>
              <span>Downloads</span>
              <span className="small muted num">
                {data.downloads.completed} finished · {data.downloads.failed} failed · {data.downloads.requests} new requests
              </span>
            </li>
            <li>
              <span>Backups</span>
              <span className="small muted num">
                {data.backups.succeeded} succeeded · {data.backups.warnings} with warnings · {data.backups.failed} failed
              </span>
            </li>
            <li>
              <span>Actions</span>
              <span className="small muted num">
                {data.actions.succeeded} succeeded · {data.actions.failed} failed
              </span>
            </li>
          </ul>
          <SourceNote source="Executor's own history (Jellyfin activity log, checks, event log, qBittorrent)" at={data.to} />
        </>
      )}
    </div>
  )
}
