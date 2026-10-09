import { ArrowDown, ArrowUp, Clapperboard, Download, Inbox, MapPin, Pause, Radio, Tv } from 'lucide-react'
import { useState } from 'react'
import type { JellyfinStatus, Media as MediaData, MediaRequest, Place, QueueItem, Watching } from '../api'
import { ago, bytes, duration, rate } from '../format'
import { streamKey } from '../map/types'
import { useApp } from '../state'
import { AttachedActions } from './ActionKit'
import { Empty, Num } from './ui'

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
            <span className={`kind-badge kind-${item.kind}`}>{item.kind === 'tv' ? 'TV' : 'Movie'}</span>
            {item.seasons.length > 0 && <span className="num"> {seasons(item.seasons)}</span>}
            {item.is_4k && <span className="kind-badge">4K</span>} <span className="user-name">{item.requested_by}</span>
            {item.requested_at && ` · ${ago(item.requested_at, now)}`}
          </span>
        </span>
      </button>
    </li>
  )
}

export function RequestsCard({ data }: { data: MediaData['requests'] }) {
  const { now } = useApp()
  const c = data.counts
  return (
    <article className="edge-card card media-card">
      <div className="edge-head">
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
  return (
    <li>
      <button type="button" className={`queue-item row-btn q-${tone}`} onClick={() => open('download', item.id)}>
        <span className="queue-top">
          <span className="queue-title">
            {item.source === 'sonarr' ? <Tv size={13} /> : <Clapperboard size={13} />}
            <span>{item.title}</span>
            {item.subtitle && <span className="muted small">{item.subtitle}</span>}
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
        {(item.message || (!active && item.state)) && (
          <span className={`small ${tone === 'up' ? 'muted' : 'warn-text'}`}>
            {item.message ?? item.state?.replace(/([A-Z])/g, ' $1').toLowerCase()}
          </span>
        )}
      </button>
    </li>
  )
}

export function DownloadsCard({ data }: { data: MediaData['downloads'] }) {
  const t = data.torrents
  const moving = (t.down_bps ?? 0) > 0
  return (
    <article className="edge-card card media-card">
      <div className="edge-head">
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
      {data.queue.length > 0 ? (
        <ul className="queue">
          {data.queue.map((q) => (
            <QueueRow key={q.id} item={q} />
          ))}
        </ul>
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
              <span className="badge-soft">
                <Pause size={10} /> paused
              </span>
            )}
            {s.transcoding && <span className="badge-soft badge-warn">transcoding</span>}
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
  return (
    <article className="edge-card card media-card now-playing">
      <div className="edge-head">
        <Radio size={16} className={n ? 'pulse-icon' : ''} />
        <h3>Now playing</h3>
        <span className="small muted">{n ? `${n} ${n === 1 ? 'stream' : 'streams'}` : ''}</span>
      </div>
      {!data.ok && <p className="small warn-text">Jellyfin unavailable: {data.error}</p>}
      {data.ok && !n && <Empty>No transmissions right now.</Empty>}
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
