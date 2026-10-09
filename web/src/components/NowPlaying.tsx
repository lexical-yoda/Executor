import { Clapperboard, MapPin, Pause, Radio, Tv } from 'lucide-react'
import type { JellyfinStatus, Place, Watching } from '../api'
import { duration } from '../format'

export function placeName(p: Partial<Place> | null | undefined): string {
  if (!p || p.lat == null) return 'Unknown location'
  return [p.city, p.country_code ?? p.country].filter(Boolean).join(', ') || 'Unknown location'
}

function Stream({ s }: { s: Watching }) {
  const pct = s.progress !== null ? Math.round(s.progress * 100) : null
  const left = s.progress !== null && s.runtime_s ? Math.round(s.runtime_s * (1 - s.progress)) : null
  return (
    <li className={`stream-row${s.paused ? ' stream-paused' : ''}`}>
      <span className="stream-icon" aria-hidden="true">
        {s.kind === 'Episode' ? <Tv size={15} /> : <Clapperboard size={15} />}
      </span>
      <div className="stream-body">
        <div className="stream-line">
          <span className="user-name stream-who">{s.user}</span>
          <span className="stream-what">{s.title}</span>
        </div>
        <div className="queue-bar">
          <div className={`queue-fill${s.paused ? '' : ' queue-active'}`} style={{ width: `${pct ?? 0}%` }} />
        </div>
        <div className="stream-meta small muted">
          <span>
            <MapPin size={11} /> {placeName(s.location)}
          </span>
          {s.ip && <span className="mono">{s.ip}</span>}
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
        </div>
      </div>
    </li>
  )
}

export function NowPlaying({ data }: { data: JellyfinStatus }) {
  const n = data.watching.length
  return (
    <article className="edge-card card media-card now-playing">
      <div className="edge-head">
        <Radio size={16} className={n ? 'pulse-icon' : ''} />
        <h3>Now playing</h3>
        <span className="small muted">{n ? `${n} ${n === 1 ? 'stream' : 'streams'}` : ''}</span>
      </div>
      {!data.ok && <p className="small warn-text">Jellyfin unavailable: {data.error}</p>}
      {data.ok && !n && <p className="small muted">Nobody is watching right now.</p>}
      {n > 0 && (
        <ul className="streams-live">
          {data.watching.map((s, i) => (
            <Stream key={`${s.user_id}-${s.device}-${i}`} s={s} />
          ))}
        </ul>
      )}
    </article>
  )
}
