import { ChevronDown, Map as MapIcon, MapPin, Search, X } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { api, type JellyfinStatus, type MediaUser, type PlaceGroup, type Sighting, type Watching } from '../api'
import { ago, duration } from '../format'
import { type MapMode, type MapSelection, streamKey } from '../map/types'
import { placeName } from './NowPlaying'

const FlatMap = lazy(() => import('../map/FlatMap'))

const RANGES = [7, 30, 90] as const

/** Load on mount, when the inputs change, and then on an interval. */
function useLoad<T>(load: (() => Promise<T>) | null, deps: unknown[], intervalMs: number) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!load) {
      setData(null)
      return
    }
    let stopped = false
    const run = async () => {
      try {
        const value = await load()
        if (!stopped) {
          setData(value)
          setError(null)
        }
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err))
      }
    }
    void run()
    const id = window.setInterval(() => !document.hidden && void run(), intervalMs)
    return () => {
      stopped = true
      window.clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, error }
}

function UserPicker({
  users,
  selected,
  onChange,
}: {
  users: MediaUser[]
  selected: string | null
  onChange: (id: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const current = users.find((u) => u.id === selected)
  const shown = users.filter((u) => u.name.toLowerCase().includes(query.toLowerCase()))

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  return (
    <div className="picker" ref={box}>
      <button type="button" className="picker-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {current ? <span className="user-name">{current.name}</span> : <span>All users</span>}
        <ChevronDown size={14} />
      </button>
      {current && (
        <button
          type="button"
          className="icon-btn picker-clear"
          onClick={() => onChange(null)}
          aria-label="Show all users"
        >
          <X size={14} />
        </button>
      )}
      {open && (
        <div className="picker-menu" role="listbox">
          <label className="picker-search">
            <Search size={13} />
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a user" />
          </label>
          <button
            type="button"
            className={`picker-item${selected ? '' : ' picker-active'}`}
            onClick={() => {
              onChange(null)
              setOpen(false)
            }}
          >
            All users
          </button>
          {shown.map((u) => (
            <button
              type="button"
              key={u.id}
              className={`picker-item${u.id === selected ? ' picker-active' : ''}`}
              onClick={() => {
                onChange(u.id)
                setOpen(false)
              }}
            >
              <span className="user-name">{u.name}</span>
              <span className="small muted">
                {u.places} {u.places === 1 ? 'place' : 'places'}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function day(seconds: number): string {
  return new Date(seconds * 1000).toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

function time(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function Timeline({ sightings }: { sightings: Sighting[] }) {
  const newest = [...sightings].reverse()
  let lastDay = ''
  return (
    <ol className="timeline">
      {newest.map((s) => {
        const d = day(s.first_seen)
        const heading = d !== lastDay ? d : null
        lastDay = d
        return (
          <li key={s.id}>
            {heading && <span className="timeline-day">{heading}</span>}
            <div className="timeline-row">
              <span className="timeline-time num">
                {time(s.first_seen)}
                {s.last_seen - s.first_seen > 300 ? `–${time(s.last_seen)}` : ''}
              </span>
              <span className="timeline-place">
                <MapPin size={11} /> {placeName(s)}
              </span>
              <span className="small muted">
                {/* Device names often contain a person's name, so they blur with usernames. */}
                {s.device && <span className="user-name">{s.device}</span>}
                {s.device && s.item ? ' · ' : ''}
                {s.item} <span className="mono">{s.ip}</span>
              </span>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function PlaceDetails({ place, now, onUser }: { place: PlaceGroup; now: number; onUser: (id: string) => void }) {
  return (
    <div className="place-details">
      <h4>
        <MapPin size={14} /> {placeName(place)}
        {place.region && <span className="muted small"> · {place.region}</span>}
      </h4>
      <p className="small muted">
        {place.count} sessions · first {ago(place.first_seen * 1000, now)} · last {ago(place.last_seen * 1000, now)}
      </p>
      <ul className="place-users">
        {place.users.map((u) => (
          <li key={u.id}>
            <button type="button" className="link-btn" onClick={() => onUser(u.id)}>
              <span className="user-name">{u.name}</span>
            </button>
            <span className="small muted">
              {u.count} · {ago(u.last_seen * 1000, now)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function StreamDetails({ stream, jellyfin }: { stream: Watching; jellyfin: JellyfinStatus }) {
  const pct = stream.progress !== null ? Math.round(stream.progress * 100) : null
  const left =
    stream.progress !== null && stream.runtime_s ? Math.round(stream.runtime_s * (1 - stream.progress)) : null
  const place = stream.location
  const route = [jellyfin.origin?.label, jellyfin.hub?.label, place ? placeName(place) : 'viewer'].filter(Boolean)
  return (
    <div className="stream-details">
      <h4 className="side-title">
        <span className="user-name">{stream.user}</span> <span className="small muted">is watching</span>
      </h4>
      <p className="stream-details-title">{stream.title}</p>
      <div className="queue-bar">
        <div className={`queue-fill${stream.paused ? '' : ' queue-active'}`} style={{ width: `${pct ?? 0}%` }} />
      </div>
      <p className="small muted num">
        {stream.paused ? 'Paused' : 'Playing'}
        {pct !== null && ` · ${pct}%`}
        {left ? ` · ${duration(left)} left` : ''}
      </p>
      <dl className="facts">
        <dt>Route</dt>
        <dd>{route.join(' → ')}</dd>
        <dt>Where</dt>
        <dd>{place ? [place.city, place.region, place.country].filter(Boolean).join(', ') : 'Unknown'}</dd>
        <dt>Address</dt>
        <dd className="mono">{stream.ip ?? '—'}</dd>
        <dt>App</dt>
        <dd>
          {stream.client}
          {stream.device && (
            <>
              {' on '}
              <span className="user-name">{stream.device}</span>
            </>
          )}
        </dd>
        <dt>Playback</dt>
        <dd>{stream.transcoding ? 'Transcoding on the server' : 'Direct play'}</dd>
      </dl>
      <p className="small muted">The place comes from the viewer's address and is city level at best.</p>
    </div>
  )
}

function RouteDetails({ jellyfin, onStream }: { jellyfin: JellyfinStatus; onStream: (key: string) => void }) {
  const n = jellyfin.watching.length
  return (
    <div className="stream-details">
      <h4 className="side-title">
        {jellyfin.origin?.label ?? 'Server'} → {jellyfin.hub?.label ?? 'relay'}
      </h4>
      <p className="small muted">
        Every stream leaves the media server at {jellyfin.origin?.label ?? 'the origin'}, travels to{' '}
        {jellyfin.hub?.label ?? 'the relay'} and goes out from there to each viewer.
      </p>
      <p>{n ? `${n} ${n === 1 ? 'stream' : 'streams'} on this route now:` : 'Nobody is streaming right now.'}</p>
      <ul className="place-users">
        {jellyfin.watching.map((s) => (
          <li key={streamKey(s)}>
            <button type="button" className="link-btn" onClick={() => onStream(streamKey(s))}>
              <span className="user-name">{s.user}</span>
            </button>
            <span className="small muted">
              {s.title} · {s.location ? placeName(s.location) : 'unknown place'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Audience({ jellyfin, now }: { jellyfin: JellyfinStatus; now: number }) {
  const [mode, setMode] = useState<MapMode>('live')
  const [days, setDays] = useState<number>(30)
  const [user, setUser] = useState<string | null>(null)
  const [selected, setSelected] = useState<MapSelection | null>(null)
  const places = useLoad(() => api.mediaPlaces(days, user ?? undefined), [days, user], 60_000)
  const users = useLoad(() => api.mediaUsers(90), [], 300_000)
  const trail = useLoad(user ? () => api.mediaTrail(user, days) : null, [user, days], 60_000)
  const placeList = places.data?.places ?? []
  const sightings = trail.data?.sightings ?? []
  const top = useMemo(() => [...placeList].sort((a, b) => b.count - a.count).slice(0, 8), [placeList])
  const live = jellyfin.watching.filter((s) => !user || s.user_id?.replace(/-/g, '') === user)
  const chosen = users.data?.users.find((u) => u.id === user)

  const chooseUser = (id: string | null) => {
    setUser(id)
    setSelected(null)
  }

  const mapProps = {
    mode,
    places: placeList,
    live,
    origin: jellyfin.origin,
    hub: jellyfin.hub,
    trail: sightings,
    now,
    selected,
    onSelect: setSelected,
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>Audience</h2>
        <div className="audience-controls">
          <div className="range-tabs" role="tablist" aria-label="Map view">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'live'}
              className={mode === 'live' ? 'active' : ''}
              onClick={() => setMode('live')}
              title="The stream route and who is watching now"
            >
              Live
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'all'}
              className={mode === 'all' ? 'active' : ''}
              onClick={() => setMode('all')}
              title="Every place in the chosen range"
            >
              All places
            </button>
          </div>
          <div className="range-tabs" role="tablist">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                role="tab"
                aria-selected={days === r}
                className={days === r ? 'active' : ''}
                onClick={() => setDays(r)}
              >
                {r}d
              </button>
            ))}
          </div>
          <UserPicker users={users.data?.users ?? []} selected={user} onChange={chooseUser} />
        </div>
      </div>

      <div className="audience-grid">
        <article className="card audience-map">
          <Suspense
            fallback={
              <div className="map-loading">
                <MapIcon size={22} className="spin-slow" />
              </div>
            }
          >
            <FlatMap {...mapProps} />
          </Suspense>
          <div className="map-legend small muted">
            {jellyfin.origin && (
              <span>
                <i className="lg-origin" /> {jellyfin.origin.label}
              </span>
            )}
            {jellyfin.hub && (
              <span>
                <i className="lg-hub" /> {jellyfin.hub.label}
              </span>
            )}
            <span>
              <i className="lg-live" /> watching now
            </span>
            <span>
              <i className="lg-place" /> places
            </span>
            {user && (
              <span>
                <i className="lg-trail" /> path
              </span>
            )}
          </div>
        </article>

        <article className="card audience-side">
          {places.error && <p className="small warn-text">History unavailable: {places.error}</p>}
          {selected ? (
            <>
              <button type="button" className="link-btn small" onClick={() => setSelected(null)}>
                ← back
              </button>
              {selected.kind === 'place' && <PlaceDetails place={selected.place} now={now} onUser={chooseUser} />}
              {selected.kind === 'route' && (
                <RouteDetails jellyfin={jellyfin} onStream={(key) => setSelected({ kind: 'stream', key })} />
              )}
              {selected.kind === 'stream' &&
                (() => {
                  const stream = jellyfin.watching.find((s) => streamKey(s) === selected.key)
                  return stream ? (
                    <StreamDetails stream={stream} jellyfin={jellyfin} />
                  ) : (
                    <p className="small muted">This stream has ended.</p>
                  )
                })()}
            </>
          ) : user ? (
            <>
              <h4 className="side-title">
                <span className="user-name">{chosen?.name ?? 'User'}</span>
                <span className="small muted">
                  {' '}
                  · {new Set(sightings.map((s) => placeName(s))).size} places in {days} days
                </span>
              </h4>
              {sightings.length ? (
                <Timeline sightings={sightings} />
              ) : (
                <p className="small muted">No sightings in this range.</p>
              )}
            </>
          ) : (
            <>
              <h4 className="side-title">
                Top places <span className="small muted">· last {days} days</span>
              </h4>
              <ul className="top-places">
                {top.map((p) => (
                  <li key={`${p.lat},${p.lon}`}>
                    <button type="button" className="link-btn" onClick={() => setSelected({ kind: 'place', place: p })}>
                      {placeName(p)}
                    </button>
                    <span className="small muted num">
                      {p.users.length} {p.users.length === 1 ? 'user' : 'users'} · {p.count}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="small muted">
                {placeList.length} places
                {places.data?.unlocated ? ` · ${places.data.unlocated} sightings without a location` : ''}
              </p>
            </>
          )}
        </article>
      </div>
      <p className="attribution small muted">
        Locations are approximate (city level).{' '}
        <a href="https://db-ip.com" target="_blank" rel="noreferrer noopener">
          IP geolocation by DB-IP
        </a>{' '}
        (CC BY 4.0) · Map: Natural Earth
      </p>
    </section>
  )
}
