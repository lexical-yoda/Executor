import { ArrowDown, ArrowUp, ChevronDown, Clapperboard, Download, Inbox, MapPin, Pause, Radio, Tv } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { JellyfinStatus, Media as MediaData, MediaRequest, Place, QueueItem, Watching } from '../api'
import { ago, bytes, duration, rate } from '../format'
import { streamKey } from '../map/types'
import { useApp, useClock } from '../state'
import { AttachedActions } from './ActionKit'
import { Empty, Num } from './ui'
import { Badge } from './Badge'

export function placeName(p: Partial<Place> | null | undefined): string {
  if (!p || p.lat == null) return 'Unknown location'
  return [p.city, p.country_code ?? p.country].filter(Boolean).join(', ') || 'Unknown location'
}

export function seasons(list: number[]): string {
  if (!list.length) return ''
  if (list.length === 1) return `S${list[0]}`
  const contiguous = list.every((n, i) => i === 0 || n === list[i - 1] + 1)
  return contiguous ? `S${list[0]}–${list[list.length - 1]}` : `${list.length} seasons`
}

export function Poster({ item, large = false }: { item: MediaRequest; large?: boolean }) {
  const [failed, setFailed] = useState(false)
  if (!item.has_poster || failed) {
    return (
      <span className={`poster poster-empty${large ? ' poster-large' : ''}`} aria-hidden="true">
        {item.kind === 'tv' ? <Tv size={16} /> : <Clapperboard size={16} />}
      </span>
    )
  }
  return (
    <img
      className={`poster${large ? ' poster-large' : ''}`}
      src={`/api/media/poster/${item.kind}/${item.tmdb_id}`}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  )
}

function RequestRow({ item, now, compact }: { item: MediaRequest; now: number; compact?: boolean }) {
  const { open } = useApp()
  return (
    <li>
      <button type="button" className={`request row-btn${compact ? ' request-compact' : ''}`} onClick={() => open('request', String(item.id))}>
        <Poster item={item} />
        <span className="request-text">
          <span className="request-title">
            {item.title}
            {item.year && <span className="muted"> ({item.year})</span>}
          </span>
          <span className="small muted">
            <Badge tone={item.kind === 'tv' ? 'info' : 'accent'}>{item.kind === 'tv' ? 'TV' : 'Movie'}</Badge>
            {item.seasons.length > 0 && <span className="num"> {seasons(item.seasons)}</span>}
            {item.is_4k && <Badge>4K</Badge>} <span className="user-name">{item.requested_by}</span>
            {item.requested_at && ` · ${ago(item.requested_at, now)}`}
          </span>
        </span>
      </button>
    </li>
  )
}

export function RequestsCard({ data }: { data: MediaData['requests'] }) {
  const now = useClock()
  const c = data.counts
  return (
    <article className="edge-card card media-card">
      <div className="card-head">
        <Inbox size={16} />
        <h3>Requests</h3>
      </div>
      {!data.ok && <p className="small warn-text">Jellyseerr unavailable: {data.error}</p>}
      <div className="stat-chips">
        <span className={`stat-chip${c.pending ? ' chip-attention' : ''}`}>
          <Num value={c.pending ?? null} /> waiting for approval
        </span>
        <span className="stat-chip">
          <Num value={c.processing ?? null} /> on the way
        </span>
        <span className="stat-chip">
          <Num value={c.available ?? null} /> available
        </span>
      </div>
      {data.pending.length > 0 ? (
        <ul className="requests">
          {data.pending.map((r) => (
            <RequestRow key={r.id} item={r} now={now} />
          ))}
        </ul>
      ) : (
        data.ok && <Empty>Nothing waiting for approval.</Empty>
      )}
      {data.processing.length > 0 && (
        <>
          <h4 className="media-sub">Approved, on the way</h4>
          <ul className="requests">
            {data.processing.map((r) => (
              <RequestRow key={r.id} item={r} now={now} compact />
            ))}
          </ul>
        </>
      )}
      <AttachedActions target="requests" />
    </article>
  )
}

export function queueTone(item: QueueItem): 'up' | 'degraded' | 'down' {
  return item.health === 'error' || item.status === 'failed' ? 'down' : item.health === 'warning' ? 'degraded' : 'up'
}

function QueueRow({ item }: { item: QueueItem }) {
  const { open } = useApp()
  const pctDone = item.progress !== null ? Math.round(item.progress * 100) : null
  const active = item.status === 'downloading'
  const tone = queueTone(item)
  const state = item.message ?? (!active && item.state ? item.state.replace(/([A-Z])/g, ' $1').toLowerCase() : null)
  return (
    <li>
      <button type="button" className={`queue-item row-btn q-${tone}`} onClick={() => open('download', item.id)}>
        <span className="queue-top">
          <span className="queue-title">
            {item.source === 'sonarr' ? <Tv size={13} /> : <Clapperboard size={13} />}
            <span>{item.title}</span>
          </span>
          <span className="queue-meta small muted num">
            {pctDone !== null && `${pctDone}%`}
            {active && item.eta_s ? ` · ${duration(item.eta_s)} left` : ''}
            {item.size ? ` · ${bytes(item.size)}` : ''}
          </span>
        </span>
        <span className="queue-bar">
          <span className={`queue-fill${active ? ' queue-active' : ''}`} style={{ width: `${pctDone ?? 0}%` }} />
        </span>
        {(item.subtitle || state) && (
          <span className="queue-sub small">
            {item.subtitle && <span className="muted">{item.subtitle}</span>}
            {state && <span className={tone === 'up' ? 'muted' : 'warn-text'}>{state}</span>}
          </span>
        )}
      </button>
    </li>
  )
}

/** Episodes of one show downloading together, as one row that opens up. */
function ShowRow({ series, items }: { series: string; items: QueueItem[] }) {
  const [opened, setOpened] = useState(false)
  const size = items.reduce((sum, i) => sum + (i.size || 0), 0)
  const done = items.reduce((sum, i) => sum + (i.size || 0) * (i.progress ?? 0), 0)
  const pctDone = size ? Math.round((done / size) * 100) : null
  const active = items.some((i) => i.status === 'downloading')
  const eta = Math.max(0, ...items.filter((i) => i.status === 'downloading').map((i) => i.eta_s ?? 0))
  const tones = items.map(queueTone)
  const tone = tones.includes('down') ? 'down' : tones.includes('degraded') ? 'degraded' : 'up'
  const stuck = items.filter((i) => queueTone(i) !== 'up').length
  const codes = items.map((i) => i.episode).filter(Boolean).sort() as string[]
  return (
    <li>
      <button type="button" className={`queue-item row-btn q-${tone}`} onClick={() => setOpened((v) => !v)} aria-expanded={opened}>
        <span className="queue-top">
          <span className="queue-title">
            <Tv size={13} />
            <span>{series}</span>
          </span>
          <span className="queue-meta small muted num">
            {pctDone !== null && `${pctDone}%`}
            {active && eta ? ` · ${duration(eta)} left` : ''}
            {size ? ` · ${bytes(size)}` : ''}
          </span>
        </span>
        <span className="queue-bar">
          <span className={`queue-fill${active ? ' queue-active' : ''}`} style={{ width: `${pctDone ?? 0}%` }} />
        </span>
        <span className="queue-sub small">
          <span className="muted">
            {items.length} episodes{codes.length > 1 ? ` · ${codes[0]} to ${codes[codes.length - 1]}` : ''}
          </span>
          {stuck > 0 && <span className="warn-text">{stuck} need a look</span>}
          <ChevronDown size={13} className={`queue-chevron${opened ? ' open' : ''}`} aria-hidden="true" />
        </span>
      </button>
      {opened && (
        <ul className="queue queue-nested">
          {items.map((q) => (
            <QueueRow key={q.id} item={q} />
          ))}
        </ul>
      )}
    </li>
  )
}

type QueueEntry = { kind: 'one'; item: QueueItem } | { kind: 'show'; series: string; items: QueueItem[] }

// The show of a queue item; older servers send only the "Show S01E02" title.
const seriesOf = (q: QueueItem) => q.series ?? (q.source === 'sonarr' ? q.title.replace(/ S\d+E\d+$/, '') : null)

/** Queue items in order, with every show that has several episodes folded into one entry. */
function groupQueue(raw: QueueItem[]): QueueEntry[] {
  const queue = raw.map((q) => ({ ...q, series: seriesOf(q), episode: q.episode ?? q.title.match(/S\d+E\d+$/)?.[0] ?? null }))
  const counts = new Map<string, number>()
  for (const q of queue) if (q.series) counts.set(q.series, (counts.get(q.series) ?? 0) + 1)
  const shows = new Map<string, QueueEntry & { kind: 'show' }>()
  const out: QueueEntry[] = []
  for (const q of queue) {
    if (q.series && (counts.get(q.series) ?? 0) > 1) {
      const show = shows.get(q.series)
      if (show) show.items.push(q)
      else {
        const entry = { kind: 'show' as const, series: q.series, items: [q] }
        shows.set(q.series, entry)
        out.push(entry)
      }
    } else out.push({ kind: 'one', item: q })
  }
  return out
}

// Rows shown before "Show all": a long queue should not outgrow its neighbours.
const QUEUE_ROWS = 8

export function DownloadsCard({ data }: { data: MediaData['downloads'] }) {
  const t = data.torrents
  const moving = (t.down_bps ?? 0) > 0
  const entries = useMemo(() => groupQueue(data.queue), [data.queue])
  const [showAll, setShowAll] = useState(false)
  return (
    <article className="edge-card card media-card">
      <div className="card-head">
        <Download size={16} />
        <h3>Downloads</h3>
        {t.configured && t.ok && (
          <span className="speeds">
            <span className={`speed${moving ? ' speed-live' : ''}`}>
              <ArrowDown size={13} /> <Num value={t.down_bps ?? 0} format={rate} />
            </span>
            <span className="speed">
              <ArrowUp size={13} /> <Num value={t.up_bps ?? 0} format={rate} />
            </span>
          </span>
        )}
      </div>
      {t.configured && !t.ok && <p className="small warn-text">qBittorrent unavailable: {t.error}</p>}
      {Object.entries(data.errors).map(([source, error]) => (
        <p key={source} className="small warn-text">
          {error}
        </p>
      ))}
      {t.configured && t.ok && (
        <div className="stat-chips">
          <span className="stat-chip">
            <Num value={t.downloading ?? 0} /> downloading
          </span>
          <span className="stat-chip">
            <Num value={t.seeding ?? 0} /> seeding
          </span>
          {!!t.stalled && (
            <span className="stat-chip chip-attention">
              <span className="num">{t.stalled}</span> stalled
            </span>
          )}
          {!!t.errored && (
            <span className="stat-chip chip-bad">
              <span className="num">{t.errored}</span> errored
            </span>
          )}
          {t.connection && t.connection !== 'connected' && <span className="stat-chip chip-attention">{t.connection}</span>}
        </div>
      )}
      {entries.length > 0 ? (
        <>
          <ul className="queue">
            {(showAll ? entries : entries.slice(0, QUEUE_ROWS)).map((e) =>
              e.kind === 'show' ? (
                <ShowRow key={e.series} series={e.series} items={e.items} />
              ) : (
                <QueueRow key={e.item.id} item={e.item} />
              ),
            )}
          </ul>
          {entries.length > QUEUE_ROWS && (
            <button type="button" className="link-btn small" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Show fewer' : `Show all ${entries.length} rows (${data.queue.length} items)`}
            </button>
          )}
        </>
      ) : (
        <Empty>Hyperspace lanes are clear: nothing in the queue.</Empty>
      )}
      <AttachedActions target="downloads" />
    </article>
  )
}

function StreamRow({ s }: { s: Watching }) {
  const { open } = useApp()
  const pct = s.progress !== null ? Math.round(s.progress * 100) : null
  const left = s.progress !== null && s.runtime_s ? Math.round(s.runtime_s * (1 - s.progress)) : null
  return (
    <li>
      <button type="button" className={`stream-row row-btn${s.paused ? ' stream-paused' : ''}`} onClick={() => open('stream', streamKey(s))}>
        <span className="stream-icon" aria-hidden="true">
          {s.kind === 'Episode' ? <Tv size={15} /> : <Clapperboard size={15} />}
        </span>
        <span className="stream-body">
          <span className="stream-line">
            <span className="user-name stream-who">{s.user}</span>
            <span className="stream-what">{s.title}</span>
          </span>
          <span className="queue-bar">
            <span className={`queue-fill${s.paused ? '' : ' queue-active'}`} style={{ width: `${pct ?? 0}%` }} />
          </span>
          <span className="stream-meta small muted">
            <span>
              <MapPin size={11} /> {placeName(s.location)}
            </span>
            <span>
              {s.client}
              {s.device && (
                <>
                  {s.client ? ' · ' : ''}
                  <span className="user-name">{s.device}</span>
                </>
              )}
            </span>
            {s.paused && (
              <Badge>
                <Pause size={10} /> Paused
              </Badge>
            )}
            {s.transcoding && <Badge tone="warn">Transcoding</Badge>}
            {pct !== null && (
              <span className="num">
                {pct}%{left ? ` · ${duration(left)} left` : ''}
              </span>
            )}
          </span>
        </span>
      </button>
    </li>
  )
}

export function NowPlayingCard({ data, limit }: { data: JellyfinStatus; limit?: number }) {
  const n = data.watching.length
  const shown = limit ? data.watching.slice(0, limit) : data.watching
  if (data.ok && !n) {
    // Nobody watching: one slim line instead of a card full of nothing.
    return (
      <article className="card now-playing-idle">
        <Radio size={15} className="muted" />
        <span className="small muted">Now playing: nobody is watching right now.</span>
        <AttachedActions target="now-playing" />
      </article>
    )
  }
  return (
    <article className="edge-card card media-card now-playing">
      <div className="card-head">
        <Radio size={16} className={n ? 'pulse-icon' : ''} />
        <h3>Now playing</h3>
        <span className="small muted">{n ? `${n} ${n === 1 ? 'stream' : 'streams'}` : ''}</span>
      </div>
      {!data.ok && <p className="small warn-text">Jellyfin unavailable: {data.error}</p>}
      {n > 0 && (
        <ul className="streams-live">
          {shown.map((s, i) => (
            <StreamRow key={`${streamKey(s)}-${i}`} s={s} />
          ))}
        </ul>
      )}
      {limit && n > limit && <p className="small muted">and {n - limit} more</p>}
      <AttachedActions target="now-playing" />
    </article>
  )
}
