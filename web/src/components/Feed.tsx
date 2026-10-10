import {
  Activity,
  Archive,
  Box,
  Cpu,
  Database,
  Download,
  Images,
  Inbox,
  Loader2,
  Play,
  Power,
  ScrollText,
  ShieldAlert,
  ShieldBan,
  ShieldCheck,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type LogEvent } from '../api'
import { ago } from '../format'
import { parseRef } from '../route'
import { useApp, useClock } from '../state'
import { Empty } from './ui'

const ICONS: Record<string, typeof Activity> = {
  service: Activity,
  machine: Cpu,
  container: Box,
  backup: Archive,
  stream: Play,
  download: Download,
  request: Inbox,
  action: Zap,
  certificate: ShieldCheck,
  photos: Images,
  storage: Database,
  dns: ShieldBan,
  attack: ShieldAlert,
  system: Power,
}

export function useEvents(intervalMs = 15_000, limit = 40) {
  const [events, setEvents] = useState<LogEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    const load = () =>
      api
        .events(undefined, limit)
        .then((r) => {
          if (live) {
            setEvents(r.events)
            setError(null)
          }
        })
        .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    void load()
    const id = window.setInterval(() => !document.hidden && void load(), intervalMs)
    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [intervalMs, limit])
  return { events, error }
}

export function EventRow({ event }: { event: LogEvent }) {
  const { openRef } = useApp()
  const now = useClock()
  const Icon = ICONS[event.kind] ?? ScrollText
  const ref = parseRef(event.ref)
  const body = (
    <>
      <span className={`ev-icon ev-${event.level}`}>
        <Icon size={13} />
      </span>
      <span className="ev-text">
        <span className="ev-title">
          {event.actor && <span className="user-name ev-actor">{event.actor} </span>}
          {event.title}
        </span>
        {event.detail && <span className="small muted ev-detail">{event.detail}</span>}
      </span>
      <span className="small muted ev-time num" title={new Date(event.ts * 1000).toLocaleString()}>
        {ago(event.ts * 1000, now)}
      </span>
    </>
  )
  return (
    <li className="ev">
      {ref ? (
        <button type="button" className="row-btn ev-row" onClick={() => openRef(ref, ref.kind === 'user' ? 'holonet' : undefined)}>
          {body}
        </button>
      ) : (
        <div className="ev-row">{body}</div>
      )}
    </li>
  )
}

export function FeedList({ events, limit }: { events: LogEvent[]; limit?: number }) {
  const shown = limit ? events.slice(0, limit) : events
  if (!shown.length) return <Empty>Quiet on all decks: nothing has happened yet.</Empty>
  return (
    <ol className="feed">
      {shown.map((e) => (
        <EventRow key={e.id} event={e} />
      ))}
    </ol>
  )
}

export function FeedDrawer() {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const more = useCallback(async (before?: number) => {
    setLoading(true)
    try {
      const r = await api.events(before, 60)
      setEvents((prev) => (before ? [...prev, ...r.events] : r.events))
      if (r.events.length < 60) setDone(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void more()
  }, [more])

  return (
    <div>
      <div className="drawer-kicker">
        <ScrollText size={14} /> Ship's log
      </div>
      <h3 className="drawer-title">Everything that happened</h3>
      {error && <p className="error small">{error}</p>}
      <FeedList events={events} />
      {!done && events.length > 0 && (
        <button type="button" className="btn btn-ghost btn-small" disabled={loading} onClick={() => void more(events[events.length - 1].id)}>
          {loading ? <Loader2 size={14} className="spin" /> : null} Older events
        </button>
      )}
      <p className="small muted">Kept for 180 days. Changes count once they hold for two checks.</p>
    </div>
  )
}
