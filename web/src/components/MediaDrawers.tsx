import { Clapperboard, Loader2, Map as MapIcon, MapPin, Tv } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type MediaUser, type Place, type PlaceGroup, type Sighting } from '../api'
import { ago, bytes, duration } from '../format'
import { placeKey, streamKey } from '../map/types'
import { useApp, useClock } from '../state'
import { AttachedActions } from './ActionKit'
import { placeName, Poster, queueTone, seasons } from './Media'
import { Empty, Facts, RowButton, SourceNote } from './ui'

/** Load once per input change. */
function useOnce<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    load()
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, error }
}

export const SOURCE_NAMES: Record<string, string> = { geolite2: 'GeoLite2', dbip: 'DB-IP' }

export function sourceNote(place: Partial<Place>): string {
  if (place.source === 'home') return 'same address as the server'
  if (place.source === 'correction') return 'your correction'
  const name = SOURCE_NAMES[place.source ?? ''] ?? 'database'
  if (place.within) {
    return `${name}, inside ${SOURCE_NAMES[place.within.source] ?? 'another database'}'s ${place.within.km} km area`
  }
  return place.radius_km ? `${name}, within about ${place.radius_km} km` : name
}

function day(seconds: number): string {
  return new Date(seconds * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}

function time(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function Timeline({ sightings, onPlace }: { sightings: Sighting[]; onPlace: (s: Sighting) => void }) {
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
            <button type="button" className="timeline-row row-btn" onClick={() => onPlace(s)} disabled={s.lat == null}>
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
            </button>
          </li>
        )
      })}
    </ol>
  )
}

export function StreamDrawer({ id }: { id: string }) {
  const { snapshot, open } = useApp()
  const jellyfin = snapshot?.jellyfin
  const stream = jellyfin?.watching.find((s) => streamKey(s) === id)
  if (!jellyfin || !stream) return <Empty>This stream has ended.</Empty>
  const pct = stream.progress !== null ? Math.round(stream.progress * 100) : null
  const left = stream.progress !== null && stream.runtime_s ? Math.round(stream.runtime_s * (1 - stream.progress)) : null
  const place = stream.location
  const route = [jellyfin.origin?.label, jellyfin.hub?.label, place ? placeName(place) : 'viewer'].filter(Boolean)
  const userId = stream.user_id?.replace(/-/g, '')
  return (
    <div className="stream-details">
      <div className="drawer-kicker">
        {stream.kind === 'Episode' ? <Tv size={14} /> : <Clapperboard size={14} />} {stream.paused ? 'Paused' : 'Watching now'}
      </div>
      <h3 className="drawer-title">
        <span className="user-name">{stream.user}</span>
      </h3>
      <p className="stream-details-title">{stream.title}</p>
      <div className="queue-bar">
        <div className={`queue-fill${stream.paused ? '' : ' queue-active'}`} style={{ width: `${pct ?? 0}%` }} />
      </div>
      <p className="small muted num">
        {stream.paused ? 'Paused' : 'Playing'}
        {pct !== null && ` · ${pct}%`}
        {left ? ` · ${duration(left)} left` : ''}
      </p>
      <Facts
        items={[
          ['Route', route.join(' → ')],
          [
            'Where',
            <>
              {place ? [place.city, place.region, place.country].filter(Boolean).join(', ') : 'Unknown'}
              {place && <span className="small muted"> · {sourceNote(place)}</span>}
              {stream.location_alt && (
                <span className="small muted">
                  {' '}
                  ({SOURCE_NAMES[stream.location_alt.source ?? ''] ?? 'The other database'} says {placeName(stream.location_alt)})
                </span>
              )}
            </>,
          ],
          ['Address', <span className="mono">{stream.ip ?? '—'}</span>],
          [
            'App',
            <>
              {stream.client}
              {stream.device && (
                <>
                  {' on '}
                  <span className="user-name">{stream.device}</span>
                </>
              )}
            </>,
          ],
          ['Playback', stream.transcoding ? 'Transcoding on the server' : 'Direct play'],
        ]}
      />
      <div className="drawer-links">
        {userId && (
          <button type="button" className="btn btn-ghost btn-small" onClick={() => open('user', userId, 'holonet')}>
            <MapIcon size={14} /> Their places and path
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-small" onClick={() => open('route', '')}>
          The route
        </button>
      </div>
      <AttachedActions target="now-playing" />
      {place?.source !== 'correction' && place?.source !== 'home' && (
        <p className="small muted">
          The place is looked up from the viewer's address and is city level at best. Add a correction in the config for
          people and devices you know.
        </p>
      )}
      <SourceNote source="Jellyfin sessions, sampled every 30 seconds" at={jellyfin.checked_at} />
    </div>
  )
}

export function RouteDrawer() {
  const { snapshot, open } = useApp()
  const jellyfin = snapshot?.jellyfin
  if (!jellyfin) return <Empty>Jellyfin is not configured.</Empty>
  const n = jellyfin.watching.length
  const nodes = [jellyfin.origin, jellyfin.hub].filter((x) => x?.machine)
  return (
    <div>
      <div className="drawer-kicker">Stream route</div>
      <h3 className="drawer-title">
        {jellyfin.origin?.label ?? 'Server'} → {jellyfin.hub?.label ?? 'relay'}
      </h3>
      <p className="muted">
        Every stream leaves the media server at {jellyfin.origin?.label ?? 'the origin'}, travels to{' '}
        {jellyfin.hub?.label ?? 'the relay'} and goes out from there to each viewer.
      </p>
      {nodes.map((node) => (
        <RowButton key={node!.machine!} onClick={() => open('machine', node!.machine!)}>
          <span>{node!.label}</span>
          <span className="small muted">machine details</span>
        </RowButton>
      ))}
      <h4 className="drawer-sub">{n ? `${n} ${n === 1 ? 'stream' : 'streams'} on this route now` : 'Nobody is streaming right now.'}</h4>
      <ul className="place-users">
        {jellyfin.watching.map((s) => (
          <li key={streamKey(s)}>
            <RowButton onClick={() => open('stream', streamKey(s))}>
              <span className="user-name">{s.user}</span>
              <span className="small muted">
                {s.title} · {s.location ? placeName(s.location) : 'unknown place'}
              </span>
            </RowButton>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function PlaceDrawer({ id }: { id: string }) {
  const { snapshot, open } = useApp()
  const now = useClock()
  const [lat, lon] = id.split(',').map(Number)
  const { data, error } = useOnce(() => api.mediaPlaces(90), [id])
  const place: PlaceGroup | undefined = data?.places.find((p) => placeKey(p) === id)
  const here = (snapshot?.jellyfin?.watching ?? []).filter((s) => s.location?.lat === lat && s.location?.lon === lon)
  const label = place ? placeName(place) : here[0] ? placeName(here[0].location) : 'Place'
  return (
    <div className="place-details">
      <div className="drawer-kicker">
        <MapPin size={14} /> Place
      </div>
      <h3 className="drawer-title">{label}</h3>
      {place?.region && <p className="muted">{[place.region, place.country].filter(Boolean).join(', ')}</p>}
      {here.length > 0 && (
        <>
          <h4 className="drawer-sub">Watching from here now</h4>
          <ul className="place-users">
            {here.map((s) => (
              <li key={streamKey(s)}>
                <RowButton onClick={() => open('stream', streamKey(s))}>
                  <span className="user-name">{s.user}</span>
                  <span className="small muted">{s.title}</span>
                </RowButton>
              </li>
            ))}
          </ul>
        </>
      )}
      {!data && !error && <Loader2 size={14} className="spin" />}
      {error && <p className="error small">{error}</p>}
      {place && (
        <>
          <Facts
            items={[
              ['Sessions', `${place.count} in 90 days`],
              ['First seen', ago(place.first_seen * 1000, now)],
              ['Last seen', ago(place.last_seen * 1000, now)],
              ['Accuracy', place.corrected ? 'includes your corrections' : place.radius_km ? `within about ${place.radius_km} km` : null],
            ]}
          />
          <h4 className="drawer-sub">People seen here</h4>
          <ul className="place-users">
            {place.users.map((u) => (
              <li key={u.id}>
                <RowButton onClick={() => open('user', u.id, 'holonet')}>
                  <span className="user-name">{u.name}</span>
                  <span className="small muted">
                    {u.count} · {ago(u.last_seen * 1000, now)}
                  </span>
                </RowButton>
              </li>
            ))}
          </ul>
        </>
      )}
      {data && !place && !here.length && <Empty>No sessions from here in the last 90 days.</Empty>}
      <SourceNote source="Executor's location history (Jellyfin sessions and activity log)" />
    </div>
  )
}

export function UserDrawer({ id }: { id: string }) {
  const { route, open } = useApp()
  const trail = useOnce(() => api.mediaTrail(id, 90), [id])
  const users = useOnce(() => api.mediaUsers(90), [])
  const user: MediaUser | undefined = users.data?.users.find((u) => u.id === id)
  const sightings = trail.data?.sightings ?? []
  const places = new Set(sightings.map((s) => placeName(s)))
  const devices = new Set(sightings.map((s) => s.device).filter(Boolean))
  return (
    <div>
      <div className="drawer-kicker">
        <MapIcon size={14} /> Viewer · last 90 days
      </div>
      <h3 className="drawer-title">
        <span className="user-name">{user?.name ?? sightings[0]?.user_name ?? 'User'}</span>
      </h3>
      {route.deck !== 'holonet' && (
        <button type="button" className="btn btn-ghost btn-small" onClick={() => open('user', id, 'holonet')}>
          <MapIcon size={14} /> Show the path on the map
        </button>
      )}
      {route.deck === 'holonet' && <p className="small muted">Their path is drawn on the map; press play there to replay it.</p>}
      <Facts
        items={[
          ['Sessions', String(sightings.length)],
          ['Places', String(places.size)],
          ['Devices', devices.size ? String(devices.size) : null],
          ['Last seen', user ? new Date(user.last_seen * 1000).toLocaleString() : null],
        ]}
      />
      {trail.error && <p className="error small">{trail.error}</p>}
      {!trail.data && !trail.error && <Loader2 size={14} className="spin" />}
      {sightings.length > 0 && (
        <Timeline sightings={sightings} onPlace={(s) => s.lat != null && open('place', `${s.lat},${s.lon}`)} />
      )}
      {trail.data && !sightings.length && <Empty>No sightings in this range.</Empty>}
    </div>
  )
}

export function RequestDrawer({ id }: { id: string }) {
  const { snapshot } = useApp()
  const now = useClock()
  const requests = snapshot?.media?.requests
  const item = [...(requests?.pending ?? []), ...(requests?.processing ?? [])].find((r) => String(r.id) === id)
  if (!item) return <Empty>This request is no longer pending or on its way; it may be available now.</Empty>
  const pending = requests?.pending.some((r) => r.id === item.id)
  return (
    <div className="request-drawer">
      <div className="drawer-kicker">Request · {pending ? 'waiting for approval' : 'approved, on the way'}</div>
      <div className="request-hero">
        <Poster item={item} large />
        <div>
          <h3 className="drawer-title">
            {item.title} {item.year && <span className="muted">({item.year})</span>}
          </h3>
          <Facts
            items={[
              ['Kind', item.kind === 'tv' ? 'TV series' : 'Movie'],
              ['Seasons', item.seasons.length ? seasons(item.seasons) : null],
              ['Quality', item.is_4k ? '4K' : null],
              ['Requested by', <span className="user-name">{item.requested_by}</span>],
              ['When', item.requested_at ? ago(item.requested_at, now) : null],
            ]}
          />
        </div>
      </div>
      <AttachedActions target="requests" />
      <SourceNote source="Jellyseerr" />
    </div>
  )
}

export function DownloadDrawer({ id }: { id: string }) {
  const { snapshot } = useApp()
  const item = snapshot?.media?.downloads.queue.find((q) => q.id === id)
  if (!item) return <Empty>This download has left the queue (finished, imported or removed).</Empty>
  const pct = item.progress !== null ? Math.round(item.progress * 100) : null
  const tone = queueTone(item)
  return (
    <div>
      <div className="drawer-kicker">
        Download · {item.source === 'sonarr' ? 'Sonarr' : 'Radarr'} <span className={`dot dot-${tone}`} />
      </div>
      <h3 className="drawer-title">{item.title}</h3>
      {item.subtitle && <p className="muted">{item.subtitle}</p>}
      <div className="queue-bar">
        <div className={`queue-fill${item.status === 'downloading' ? ' queue-active' : ''}`} style={{ width: `${pct ?? 0}%` }} />
      </div>
      <Facts
        items={[
          ['Progress', pct !== null ? `${pct}%` : null],
          ['Size', item.size ? bytes(item.size) : null],
          ['Time left', item.eta_s ? duration(item.eta_s) : null],
          ['Status', item.status],
          ['Stage', item.state?.replace(/([A-Z])/g, ' $1').toLowerCase()],
          ['Health', item.health],
          ['Client', item.client],
          ['Message', item.message],
        ]}
      />
      <AttachedActions target="downloads" />
      <SourceNote source={item.source === 'sonarr' ? 'Sonarr queue' : 'Radarr queue'} />
    </div>
  )
}
