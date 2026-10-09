import { ChevronDown, Map as MapIcon, MapPin, Pause, Play, Search, SkipBack, SkipForward, Users, X } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { api, type MediaUser } from '../api'
import { ago } from '../format'
import { type MapMode, placeKey, trailHops } from '../map/types'
import { useApp } from '../state'
import { LibraryCard } from '../components/Library'
import { DownloadsCard, NowPlayingCard, placeName, RequestsCard } from '../components/Media'
import { Empty, RowButton } from '../components/ui'
import { nodeStatuses } from './Bridge'

const MapView = lazy(() => import('../map/MapView'))
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
        <Users size={13} />
        {current ? <span className="user-name">{current.name}</span> : <span>Everyone</span>}
        <ChevronDown size={14} />
      </button>
      {current && (
        <button type="button" className="icon-btn picker-clear" onClick={() => onChange(null)} aria-label="Show everyone">
          <X size={14} />
        </button>
      )}
      {open && (
        <div className="picker-menu" role="listbox">
          <label className="picker-search">
            <Search size={13} />
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a viewer" />
          </label>
          <button
            type="button"
            className={`picker-item${selected ? '' : ' picker-active'}`}
            onClick={() => {
              onChange(null)
              setOpen(false)
            }}
          >
            Everyone
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

export function Holonet({ tour }: { tour: boolean }) {
  const { snapshot, open, close, route, now } = useApp()
  const s = snapshot!
  const j = s.jellyfin
  const media = s.media
  const history = !!j?.history.enabled
  const [mode, setMode] = useState<MapMode>('live')
  const [days, setDays] = useState<number>(30)
  const user = route.drawer?.kind === 'user' ? route.drawer.id : null
  const selected = route.drawer?.kind === 'stream' ? route.drawer.id : null
  const places = useLoad(history ? () => api.mediaPlaces(days, user ?? undefined) : null, [days, user, history], 60_000)
  const users = useLoad(history ? () => api.mediaUsers(90) : null, [history], 300_000)
  const trail = useLoad(user ? () => api.mediaTrail(user, days) : null, [user, days], 60_000)
  const sightings = useMemo(() => trail.data?.sightings ?? [], [trail.data])
  const hops = useMemo(() => trailHops(sightings), [sightings])
  const [replay, setReplay] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const statuses = useMemo(() => nodeStatuses(snapshot), [snapshot])
  const live = (j?.watching ?? []).filter((w) => !user || w.user_id?.replace(/-/g, '') === user)
  const placeList = places.data?.places ?? []
  const top = useMemo(() => [...placeList].sort((a, b) => b.count - a.count).slice(0, 10), [placeList])

  useEffect(() => {
    setReplay(null)
    setPlaying(false)
  }, [user, days])

  useEffect(() => {
    if (!playing || hops.length < 2) return
    const id = window.setInterval(() => {
      setReplay((i) => {
        const next = i == null ? 0 : i + 1
        if (next >= hops.length) {
          setPlaying(false)
          return hops.length - 1
        }
        return next
      })
    }, 1300)
    return () => window.clearInterval(id)
  }, [playing, hops.length])

  const current = replay != null ? hops[replay] : null

  return (
    <div className="holonet">
      {j && (
        <section className="explorer card">
          <Suspense
            fallback={
              <div className="map-loading">
                <MapIcon size={22} className="spin-slow" />
              </div>
            }
          >
            <MapView
              variant="full"
              mode={mode}
              origin={j.origin}
              hub={j.hub}
              nodeStatus={statuses}
              live={live}
              places={placeList}
              trail={sightings}
              replay={replay}
              selected={selected}
              onOpen={(kind, id) => open(kind, id)}
              tour={tour}
              overlay={
                <>
                  <div className="explorer-controls">
                    <div className="range-tabs" role="tablist" aria-label="Map view">
                      <button type="button" role="tab" aria-selected={mode === 'live'} className={mode === 'live' ? 'active' : ''} onClick={() => setMode('live')}>
                        Live
                      </button>
                      <button type="button" role="tab" aria-selected={mode === 'all'} className={mode === 'all' ? 'active' : ''} onClick={() => setMode('all')}>
                        All places
                      </button>
                    </div>
                    {history && (
                      <>
                        <div className="range-tabs" role="tablist" aria-label="Days">
                          {RANGES.map((r) => (
                            <button key={r} type="button" role="tab" aria-selected={days === r} className={days === r ? 'active' : ''} onClick={() => setDays(r)}>
                              {r}d
                            </button>
                          ))}
                        </div>
                        <UserPicker
                          users={users.data?.users ?? []}
                          selected={user}
                          onChange={(id) => (id ? open('user', id) : close())}
                        />
                      </>
                    )}
                  </div>
                  <div className="map-legend small">
                    {j.origin && (
                      <span>
                        <i className="lg-origin" /> {j.origin.label}
                      </span>
                    )}
                    {j.hub && (
                      <span>
                        <i className="lg-hub" /> {j.hub.label}
                      </span>
                    )}
                    <span>
                      <i className="lg-live" /> watching now
                    </span>
                    {(mode === 'all' || user) && (
                      <span>
                        <i className="lg-place" /> places
                      </span>
                    )}
                    {user && (
                      <span>
                        <i className="lg-trail" /> path
                      </span>
                    )}
                  </div>
                  {hops.length > 1 && (
                    <div className="replay">
                      <button type="button" className="icon-btn" onClick={() => setReplay((i) => Math.max(0, (i ?? 0) - 1))} aria-label="Previous place">
                        <SkipBack size={15} />
                      </button>
                      <button
                        type="button"
                        className="icon-btn replay-play"
                        onClick={() => {
                          if (!playing && (replay == null || replay >= hops.length - 1)) setReplay(0)
                          setPlaying((p) => !p)
                        }}
                        aria-label={playing ? 'Pause' : 'Replay the path'}
                      >
                        {playing ? <Pause size={15} /> : <Play size={15} />}
                      </button>
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => setReplay((i) => Math.min(hops.length - 1, (i ?? -1) + 1))}
                        aria-label="Next place"
                      >
                        <SkipForward size={15} />
                      </button>
                      <input
                        type="range"
                        min={0}
                        max={hops.length - 1}
                        value={replay ?? 0}
                        onChange={(e) => {
                          setPlaying(false)
                          setReplay(Number(e.target.value))
                        }}
                        aria-label="Point in the path"
                      />
                      <span className="replay-label small">
                        {current ? (
                          <>
                            <b>{replay! + 1}.</b> {placeName(current.sighting)} ·{' '}
                            {new Date(current.sighting.first_seen * 1000).toLocaleString(undefined, {
                              day: 'numeric',
                              month: 'short',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </>
                        ) : (
                          `${hops.length} places · press play`
                        )}
                      </span>
                    </div>
                  )}
                </>
              }
            />
          </Suspense>
        </section>
      )}

      <div className="holonet-grid">
        {j && <NowPlayingCard data={j} />}
        {media?.requests.configured && <RequestsCard data={media.requests} />}
        {media && <DownloadsCard data={media.downloads} />}
        {s.library && <LibraryCard library={s.library} />}
        {history && (
          <article className="edge-card card media-card">
            <div className="edge-head">
              <MapPin size={16} />
              <h3>Top places</h3>
              <span className="small muted">last {days} days</span>
            </div>
            {places.error && <p className="small warn-text">History unavailable: {places.error}</p>}
            <ul className="top-places">
              {top.map((p) => (
                <li key={placeKey(p)}>
                  <RowButton onClick={() => open('place', placeKey(p))}>
                    <span>{placeName(p)}</span>
                    <span className="small muted num">
                      {p.users.length} {p.users.length === 1 ? 'viewer' : 'viewers'} · {p.count}
                    </span>
                  </RowButton>
                </li>
              ))}
            </ul>
            {!top.length && places.data && <Empty>No places in this range.</Empty>}
            <p className="small muted">
              {placeList.length} places
              {places.data?.unlocated ? ` · ${places.data.unlocated} sessions without a location` : ''}
            </p>
          </article>
        )}
        {history && (
          <article className="edge-card card media-card">
            <div className="edge-head">
              <Users size={16} />
              <h3>Viewers</h3>
              <span className="small muted">last 90 days</span>
            </div>
            <ul className="top-places viewers-list">
              {(users.data?.users ?? []).slice(0, 14).map((u) => (
                <li key={u.id}>
                  <RowButton onClick={() => open('user', u.id)}>
                    <span className="user-name">{u.name}</span>
                    <span className="small muted num">
                      {u.places} {u.places === 1 ? 'place' : 'places'} · {ago(u.last_seen * 1000, now)}
                    </span>
                  </RowButton>
                </li>
              ))}
            </ul>
          </article>
        )}
      </div>
      {j && (
        <p className="attribution small muted">
          Locations are approximate (city level) unless corrected.{' '}
          {j.history.geo_sources.some((g) => g.name === 'geolite2' && g.ready) && (
            <>
              This product includes GeoLite2 data created by MaxMind, available from{' '}
              <a href="https://www.maxmind.com" target="_blank" rel="noreferrer noopener">
                maxmind.com
              </a>
              .{' '}
            </>
          )}
          <a href="https://db-ip.com" target="_blank" rel="noreferrer noopener">
            IP geolocation by DB-IP
          </a>{' '}
          (CC BY 4.0) · Map data ©{' '}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer noopener">
            OpenStreetMap
          </a>{' '}
          contributors, tiles by{' '}
          <a href="https://protomaps.com" target="_blank" rel="noreferrer noopener">
            Protomaps
          </a>
        </p>
      )}
    </div>
  )
}
