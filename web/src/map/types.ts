import type { MapAnchor, PlaceGroup, Sighting, Status, Watching } from '../api'

// Types and helpers the rest of the page may import without pulling in the
// map engine, which loads only with the map itself.

export type MapMode = 'live' | 'all'

/** A stable key for a live stream across refreshes. */
export function streamKey(s: Watching): string {
  return `${s.user_id ?? s.user}|${s.device ?? ''}|${s.client ?? ''}`
}

export interface MapViewProps {
  /** hero: the bridge's centerpiece; full: the Holonet deck's explorer. */
  variant: 'hero' | 'full'
  mode: MapMode
  origin: MapAnchor | null
  hub: MapAnchor | null
  /** Health of the machines at the anchors, by machine id. */
  nodeStatus: Record<string, Status>
  live: Watching[]
  places: PlaceGroup[]
  trail: Sighting[]
  /** Index into the trail's hops being replayed, if any. */
  replay: number | null
  selected: string | null
  /** Open details: ("stream", key), ("place", "lat,lon"), ("route", ""), ("machine", id), ("user", id). */
  onOpen: (kind: string, id: string) => void
  /** Attract mode moves the camera on its own. */
  tour?: boolean
}

/** Distinct consecutive places in a user's trail, oldest first. */
export function trailHops(trail: Sighting[]): { lat: number; lon: number; sighting: Sighting }[] {
  const hops: { lat: number; lon: number; sighting: Sighting }[] = []
  for (const s of trail) {
    if (s.lat == null || s.lon == null) continue
    const last = hops[hops.length - 1]
    if (!last || last.lat !== s.lat || last.lon !== s.lon) hops.push({ lat: s.lat, lon: s.lon, sighting: s })
  }
  return hops
}

export function placeKey(p: { lat: number; lon: number }): string {
  return `${p.lat},${p.lon}`
}

export function recent(place: PlaceGroup, now: number): boolean {
  return now / 1000 - place.last_seen < 86400
}
