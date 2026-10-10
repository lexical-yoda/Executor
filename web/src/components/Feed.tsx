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
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type LogEvent } from '../api'
import { ago } from '../format'
import { parseRef } from '../route'
import { useApp, useClock } from '../state'
import { RangePicker } from './RangePicker'
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

// "Downloaded Show S05E16 Title" or "Grabbed Show S05E16": verb, show, episode.
const EPISODE = /^(Downloaded|Grabbed) (.+?) (S\d+E\d+)\b/
// Episodes of one show logged within this long of each other fold together.
const BURST_S = 2 * 3600

type FeedEntry = { kind: 'one'; event: LogEvent } | { kind: 'burst'; verb: string; show: string; events: LogEvent[] }

/** Runs of three or more episode events of one show fold into one line. */
function foldBursts(events: LogEvent[]): FeedEntry[] {
  const out: FeedEntry[] = []
  let run: { verb: string; show: string; events: LogEvent[] } | null = null
  const flush = () => {
    if (!run) return
    if (run.events.length >= 3) out.push({ kind: 'burst', ...run })
    else out.push(...run.events.map((event) => ({ kind: 'one' as const, event })))
    run = null
  }
  for (const e of events) {
    const m = e.kind === 'download' ? EPISODE.exec(e.title) : null
    if (m && run && run.verb === m[1] && run.show === m[2] && Math.abs(run.events[run.events.length - 1].ts - e.ts) <= BURST_S) {
      run.events.push(e)
      continue
    }
    flush()
    if (m) run = { verb: m[1], show: m[2], events: [e] }
    else out.push({ kind: 'one', event: e })
  }
  flush()
  return out
}

function BurstRow({ entry }: { entry: FeedEntry & { kind: 'burst' } }) {
  const now = useClock()
  const [opened, setOpened] = useState(false)
  const codes = entry.events.map((e) => EPISODE.exec(e.title)?.[3] ?? '').filter(Boolean).sort()
  const latest = entry.events[0]
  return (
    <li className="ev">
      <button type="button" className="row-btn ev-row" onClick={() => setOpened((v) => !v)} aria-expanded={opened}>
        <span className={`ev-icon ev-${latest.level}`}>
          <Download size={13} />
        </span>
        <span className="ev-text">
          <span className="ev-title">
            {entry.verb} {entry.events.length} episodes of {entry.show}
          </span>
          <span className="small muted ev-detail">
            {codes[0]} to {codes[codes.length - 1]} · {opened ? 'hide' : 'show'} each
          </span>
        </span>
        <span className="small muted ev-time num" title={new Date(latest.ts * 1000).toLocaleString()}>
          {ago(latest.ts * 1000, now)}
        </span>
      </button>
      {opened && (
        <ol className="feed feed-nested">
          {entry.events.map((e) => (
            <EventRow key={e.id} event={e} />
          ))}
        </ol>
      )}
    </li>
  )
}

export function FeedList({ events, limit }: { events: LogEvent[]; limit?: number }) {
  const entries = useMemo(() => foldBursts(events), [events])
  const shown = limit ? entries.slice(0, limit) : entries
  if (!shown.length) return <Empty>Quiet on all decks: nothing has happened yet.</Empty>
  return (
    <ol className="feed">
      {shown.map((entry) =>
        entry.kind === 'burst' ? (
          <BurstRow key={`burst-${entry.events[0].id}`} entry={entry} />
        ) : (
          <EventRow key={entry.event.id} event={entry.event} />
        ),
      )}
    </ol>
  )
}

// The ship's log filters: each covers a few event kinds.
const FILTERS: { value: string; label: string; kinds: string[] | null }[] = [
  { value: 'all', label: 'All', kinds: null },
  { value: 'media', label: 'Media', kinds: ['stream', 'request', 'photos'] },
  { value: 'downloads', label: 'Downloads', kinds: ['download'] },
  { value: 'systems', label: 'Systems', kinds: ['service', 'machine', 'container', 'storage', 'dns', 'certificate', 'system'] },
  { value: 'security', label: 'Security', kinds: ['attack'] },
  { value: 'actions', label: 'Actions', kinds: ['action', 'backup'] },
]

export function FeedDrawer() {
  const [filter, setFilter] = useState('all')
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

  const kinds = FILTERS.find((f) => f.value === filter)?.kinds
  const shown = kinds ? events.filter((e) => kinds.includes(e.kind)) : events

  return (
    <div>
      <div className="drawer-kicker">
        <ScrollText size={14} /> Ship's log
      </div>
      <h3 className="drawer-title">Everything that happened</h3>
      <RangePicker options={FILTERS} value={filter} onChange={setFilter} label="Show" />
      {error && <p className="error small">{error}</p>}
      <FeedList events={shown} />
      {!done && events.length > 0 && (
        <button type="button" className="btn btn-ghost btn-small" disabled={loading} onClick={() => void more(events[events.length - 1].id)}>
          {loading ? <Loader2 size={14} className="spin" /> : null} Older events
        </button>
      )}
      <p className="small muted">Kept for 180 days. Changes count once they hold for two checks.</p>
    </div>
  )
}
