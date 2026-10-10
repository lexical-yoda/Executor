import { ExternalLink, Hourglass, Images, Loader2 } from 'lucide-react'
import { type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, type DayCount, type PhotoHistory, type PhotoJob, type PhotoLibrary, type Photos } from '../api'
import { bytes, pct } from '../format'
import { useApp } from '../state'
import { day, signed, SizeChart } from './Backups'
import { Empty, Facts, Num, RowButton, SourceNote } from './ui'
import { Badge } from './Badge'
import { RangePicker } from './RangePicker'

const DAY = 86_400_000

/** A queue with something to say: busy, failed or paused. */
const working = (j: PhotoJob) => j.active + j.waiting + j.delayed + j.failed > 0 || j.paused

function count(n: number, word: string): string {
  return `${n.toLocaleString()} ${n === 1 ? word : `${word}s`}`
}

function longDay(date: string): string {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

/** What Immich's background jobs are doing, as a pill. */
function JobsPill({ lib }: { lib: PhotoLibrary }) {
  if (lib.backlog == null) return null
  if (lib.backlog > 0)
    return (
      <Badge tone="info">
        <Hourglass size={11} />
        Processing {lib.backlog.toLocaleString()}
      </Badge>
    )
  return <Badge tone="good">Idle</Badge>
}

/** Photos against videos, by the space they take. */
function SplitBar({ lib }: { lib: PhotoLibrary }) {
  const total = lib.photo_bytes + lib.video_bytes
  const share = total ? (lib.photo_bytes / total) * 100 : 50
  return (
    <div className="split">
      <div className="split-bar" aria-hidden="true">
        <span className="split-photos" style={{ width: `${share}%` }} />
        <span className="split-videos" style={{ width: `${100 - share}%` }} />
      </div>
      <div className="daily-legend small muted num">
        <span>
          <i className="split-photos" />
          Photos {bytes(lib.photo_bytes)}
        </span>
        <span>
          <i className="split-videos" />
          Videos {bytes(lib.video_bytes)}
        </span>
      </div>
    </div>
  )
}

/** Uploads per day for the last month, as small columns. */
function UploadBars({ days }: { days: DayCount[] }) {
  if (!days.length) return null
  const max = Math.max(1, ...days.map((d) => d.count))
  return (
    <svg className="upload-bars" viewBox={`0 0 ${days.length * 4} 26`} preserveAspectRatio="none" aria-hidden="true">
      {days.map((d, i) => {
        const h = d.count ? Math.max(2, (d.count / max) * 24) : 1
        return (
          <rect
            key={d.date}
            x={i * 4}
            y={26 - h}
            width={3}
            height={h}
            rx={0.8}
            className={d.count ? 'bar-on' : 'bar-off'}
          >
            <title>{`${day(d.date)}: ${d.count}`}</title>
          </rect>
        )
      })}
    </svg>
  )
}

/** The library card on the Archives deck. */
export function PhotoLibrarySection({ photos }: { photos: Photos }) {
  const { open } = useApp()
  const lib = photos.library
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      open('photos')
    }
  }
  const growth = photos.growth?.d7
  return (
    <section className="section">
      <div className="section-head">
        <h2>Photo library</h2>
        {lib?.update && <span className="small update-text">Immich {lib.latest} available</span>}
      </div>
      {!photos.configured && <p className="small warn-text">Immich is configured but IMMICH_API_KEY is not set.</p>}
      {photos.configured && photos.error && <p className="small warn-text">Immich unavailable: {photos.error}</p>}
      {lib ? (
        <article
          className={`backup-card photo-card card clickable bk-edge-${photos.ok ? 'ok' : 'warning'}`}
          onClick={() => open('photos')}
          onKeyDown={onKey}
          role="button"
          tabIndex={0}
        >
          <div className="card-head">
            <Images size={16} />
            <h3>Immich</h3>
            <JobsPill lib={lib} />
          </div>
          <div className="photo-body">
            <div className="photo-main">
              <div className="backup-main">
                <Num className="backup-big" value={lib.bytes} format={(v) => bytes(v)} />
                <span className="small muted">
                  {count(lib.photos, 'photo')} · {count(lib.videos, 'video')}
                </span>
              </div>
              <SplitBar lib={lib} />
              <div className="stat-chips">
                {growth != null && (
                  <span className="stat-chip">
                    <span className="num">{signed(growth)}</span> in 7 days
                  </span>
                )}
                {photos.added_7d != null && (
                  <span className="stat-chip">
                    <span className="num">{photos.added_7d.toLocaleString()}</span> added this week
                  </span>
                )}
                {lib.disk?.free != null && (
                  <span className="stat-chip">
                    <span className="num">{bytes(lib.disk.free)}</span> free
                  </span>
                )}
                {!!lib.failed && (
                  <span className="stat-chip warn-text">
                    <span className="num">{lib.failed}</span> failed {lib.failed === 1 ? 'job' : 'jobs'}
                  </span>
                )}
              </div>
            </div>
            {photos.recent.length > 0 && (
              <div className="photo-recent">
                <UploadBars days={photos.recent} />
                <span className="small muted">
                  {photos.added_30d != null
                    ? `${photos.added_30d.toLocaleString()} added in the last 30 days`
                    : 'Last 30 days'}
                </span>
              </div>
            )}
          </div>
        </article>
      ) : (
        photos.configured && !photos.error && <Empty>Reading the library…</Empty>
      )}
    </section>
  )
}

/** A year of days, a column per week, shaded by how much happened. */
function YearHeatmap({
  series,
  picked,
  onPick,
}: {
  series: DayCount[]
  picked: string | null
  onPick: (date: string | null) => void
}) {
  const grid = useMemo(() => {
    const first = new Date(`${series[0].date}T00:00:00Z`).getTime()
    const last = new Date(`${series[series.length - 1].date}T00:00:00Z`).getTime()
    // Weeks start on Monday.
    const start = first - ((new Date(first).getUTCDay() + 6) % 7) * DAY
    const weeks = Math.ceil((last - start + DAY) / (7 * DAY))
    const counts = new Map(series.map((p) => [p.date, p.count]))
    const busy = series
      .map((p) => p.count)
      .filter((n) => n > 0)
      .sort((a, b) => a - b)
    const cut = (f: number) => busy[Math.min(busy.length - 1, Math.floor(f * busy.length))] ?? 0
    const cuts = [cut(0.25), cut(0.5), cut(0.8)]
    const level = (n: number) => (n <= 0 ? 0 : n <= cuts[0] ? 1 : n <= cuts[1] ? 2 : n <= cuts[2] ? 3 : 4)
    const cells: { date: string; x: number; y: number; n: number; level: number }[] = []
    const months: { x: number; label: string }[] = []
    let month = -1
    for (let w = 0; w < weeks; w++) {
      const monday = new Date(start + w * 7 * DAY)
      if (monday.getUTCMonth() !== month) {
        month = monday.getUTCMonth()
        if (w > 0 || monday.getUTCDate() <= 7)
          months.push({ x: w, label: monday.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' }) })
      }
      for (let d = 0; d < 7; d++) {
        const t = start + (w * 7 + d) * DAY
        if (t < first || t > last) continue
        const date = new Date(t).toISOString().slice(0, 10)
        const n = counts.get(date) ?? 0
        cells.push({ date, x: w, y: d, n, level: level(n) })
      }
    }
    return { weeks, cells, months }
  }, [series])

  const left = 22
  const top = 16
  const size = 12
  return (
    <svg
      className="heatmap"
      viewBox={`0 0 ${left + grid.weeks * size} ${top + 7 * size}`}
      role="img"
      aria-label="Activity per day over the last year"
    >
      {grid.months.map((m) => (
        <text key={`${m.x}-${m.label}`} x={left + m.x * size} y={11} className="heatmap-label">
          {m.label}
        </text>
      ))}
      {['M', 'W', 'F'].map((label, i) => (
        <text key={label} x={0} y={top + (i * 2 + 1) * size + 8} className="heatmap-label">
          {label}
        </text>
      ))}
      {grid.cells.map((c) => (
        <rect
          key={c.date}
          x={left + c.x * size}
          y={top + c.y * size}
          width={size - 2}
          height={size - 2}
          rx={2}
          className={`hm-${c.level}${picked === c.date ? ' hm-picked' : ''}`}
          onClick={() => onPick(picked === c.date ? null : c.date)}
        >
          <title>{`${longDay(c.date)}: ${c.n.toLocaleString()}`}</title>
        </rect>
      ))}
    </svg>
  )
}

/** On a narrow screen the year scrolls sideways; start at the most recent weeks. */
function HeatmapScroller({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (box.current) box.current.scrollLeft = box.current.scrollWidth
  }, [])
  return (
    <div className="heatmap-wrap" ref={box}>
      {children}
    </div>
  )
}

function ActivitySummary({ series, picked, noun }: { series: DayCount[]; picked: string | null; noun: string }) {
  if (picked) {
    const n = series.find((p) => p.date === picked)?.count ?? 0
    return (
      <p className="small">
        {longDay(picked)} · <span className="num">{n.toLocaleString()}</span> {n === 1 ? 'item' : 'items'} {noun}
      </p>
    )
  }
  const total = series.reduce((sum, p) => sum + p.count, 0)
  const active = series.filter((p) => p.count > 0).length
  const best = series.reduce<DayCount | null>((top, p) => (!top || p.count > top.count ? p : top), null)
  return (
    <p className="small muted">
      <span className="num">{total.toLocaleString()}</span> {noun} in the last year · {count(active, 'active day')}
      {best && best.count > 0 && ` · busiest ${day(best.date)} (${best.count.toLocaleString()})`}
    </p>
  )
}

export function PhotosDrawer() {
  const { snapshot, open } = useApp()
  const photos = snapshot?.photos
  const [history, setHistory] = useState<PhotoHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [kind, setKind] = useState<'upload' | 'taken'>('upload')
  const [picked, setPicked] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    const load = () =>
      api
        .photoHistory()
        .then((h) => live && (setHistory(h), setError(null)))
        .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    void load()
    const id = window.setInterval(() => !document.hidden && void load(), 300_000)
    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [])

  if (!photos) return <Empty>Immich is not configured.</Empty>
  const lib = photos.library
  const series = history?.[kind] ?? null
  const service =
    snapshot?.services.find((s) => s.id === 'immich') ?? snapshot?.services.find((s) => /immich/i.test(s.name))
  const sizes = (history?.size ?? []).map((p) => ({ date: p.date, bytes: p.bytes }))
  return (
    <div className="photos-drawer">
      <div className="drawer-kicker">
        <Images size={14} /> Immich {lib && <JobsPill lib={lib} />}
      </div>
      <h3 className="drawer-title">Photo library</h3>
      {photos.error && <p className="small warn-text">{photos.error}</p>}
      {!lib && !photos.error && <Loader2 size={14} className="spin" />}
      {lib && (
        <>
          <div className="recap-stats photo-stats">
            <div className="recap-stat">
              <Num value={lib.bytes} format={(v) => bytes(v)} className="recap-big" />
              <span className="small muted">in the library</span>
            </div>
            <div className="recap-stat">
              <Num value={lib.photos} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
              <span className="small muted">photos</span>
            </div>
            <div className="recap-stat">
              <Num value={lib.videos} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
              <span className="small muted">videos</span>
            </div>
          </div>
          <SplitBar lib={lib} />

          <div className="drawer-row">
            <h4 className="drawer-sub">Activity</h4>
            <RangePicker
              options={[
                { value: 'upload', label: 'Added' },
                { value: 'taken', label: 'Taken' },
              ]}
              value={kind}
              onChange={(k) => (setKind(k), setPicked(null))}
              label="Activity"
            />
          </div>
          {error && <p className="small warn-text">{error}</p>}
          {!history && !error && <Loader2 size={14} className="spin" />}
          {history && !series?.length && <Empty>Immich did not share daily activity for this key.</Empty>}
          {series && series.length > 0 && (
            <>
              <HeatmapScroller>
                <YearHeatmap series={series} picked={picked} onPick={setPicked} />
              </HeatmapScroller>
              <ActivitySummary series={series} picked={picked} noun={kind === 'upload' ? 'added' : 'taken'} />
              <p className="small muted">
                {kind === 'upload'
                  ? 'By the day each item reached Immich'
                  : 'By the date each photo or video was taken'}
                , for the account the key belongs to.
              </p>
            </>
          )}

          <h4 className="drawer-sub">Growth</h4>
          {sizes.length > 1 ? (
            <SizeChart series={sizes} />
          ) : (
            <p className="small muted">
              Executor records the library size once a day; the chart fills in from{' '}
              {photos.growth ? day(photos.growth.tracked_since) : 'today'}.
            </p>
          )}
          <Facts
            items={[
              ['Last 7 days', photos.growth?.d7 != null ? signed(photos.growth.d7) : null],
              ['Last 30 days', photos.growth?.d30 != null ? signed(photos.growth.d30) : null],
              [
                'Added',
                photos.added_30d != null
                  ? `${count(photos.added_7d ?? 0, 'item')} this week · ${count(photos.added_30d, 'item')} in 30 days`
                  : null,
              ],
              [
                'Library disk',
                lib.disk?.size != null
                  ? `${bytes(lib.disk.used)} used of ${bytes(lib.disk.size)} (${pct(lib.disk.pct)}) · ${bytes(lib.disk.free)} free`
                  : null,
              ],
              [
                'Version',
                lib.version
                  ? `${lib.version}${lib.update ? ` · ${lib.latest} available` : lib.latest ? ' · up to date' : ''}`
                  : null,
              ],
            ]}
          />

          {lib.users.length > 0 && (
            <>
              <h4 className="drawer-sub">People</h4>
              <ul className="recap-list photo-users">
                {lib.users.map((u) => {
                  const max = lib.users[0].bytes || 1
                  // One person without a quota would only ever show a full bar.
                  const bar = u.quota || lib.users.length > 1
                  return (
                    <li key={u.name}>
                      <span className="user-name">{u.name}</span>
                      <span className="small muted num">
                        {bytes(u.bytes)}
                        {u.quota ? ` of ${bytes(u.quota)}` : ''} · {count(u.photos, 'photo')} ·{' '}
                        {count(u.videos, 'video')}
                      </span>
                      {bar && (
                        <span className="queue-bar">
                          <span
                            className="queue-fill photo-user-fill"
                            style={{
                              width: `${Math.min(100, Math.round((u.quota ? u.bytes / u.quota : u.bytes / max) * 100))}%`,
                            }}
                          />
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </>
          )}

          {lib.jobs && (
            <>
              <h4 className="drawer-sub">Background jobs</h4>
              {lib.jobs.some(working) && (
                <ul className="recap-list photo-jobs">
                  {lib.jobs.filter(working).map((j) => {
                    const parts = [
                      j.active && `${j.active} running`,
                      j.waiting && `${j.waiting.toLocaleString()} waiting`,
                      j.delayed && `${j.delayed} delayed`,
                    ].filter(Boolean)
                    return (
                      <li key={j.name}>
                        <span>{j.label}</span>
                        <span className="small num">
                          {j.paused && <span className="warn-text">paused · </span>}
                          <span className="muted">{parts.length ? parts.join(' · ') : 'idle'}</span>
                          {j.failed > 0 && <span className="warn-text"> · {j.failed} failed</span>}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              )}
              <p className="small muted">
                {lib.jobs.some(working)
                  ? `${count(lib.jobs.filter((j) => !working(j)).length, 'other queue')} idle.`
                  : `All caught up: nothing queued in ${count(lib.jobs.length, 'queue')}.`}
              </p>
            </>
          )}
        </>
      )}
      {service && (
        <div className="photo-links">
          <RowButton onClick={() => open('service', service.id)}>
            <span>{service.name} service</span>
            <span className="small muted">health, containers, uptime</span>
          </RowButton>
          {service.url && (
            <a className="row-btn" href={service.url} target="_blank" rel="noreferrer">
              <span>Open Immich</span>
              <ExternalLink size={13} />
            </a>
          )}
        </div>
      )}
      <SourceNote source="Immich API (read-only key)" at={photos.checked_at} />
    </div>
  )
}
