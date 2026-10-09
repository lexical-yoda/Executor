import type { PlaceGroup, Sighting, Watching } from '../api'

// Types and helpers the rest of the page may import without pulling in the
// map data, which loads only with the map itself.

export interface MapPoint {
  label: string
  lat: number
  lon: number
}

export type MapMode = 'live' | 'all'

/** What was clicked on the map: a place, one viewer's stream, or the home-to-relay route. */
export type MapSelection = { kind: 'place'; place: PlaceGroup } | { kind: 'stream'; key: string } | { kind: 'route' }

/** A stable key for a live stream across refreshes. */
export function streamKey(s: Watching): string {
  return `${s.user_id ?? s.user}|${s.device ?? ''}|${s.client ?? ''}`
}

export interface MapProps {
  mode: MapMode
  places: PlaceGroup[]
  live: Watching[]
  origin: MapPoint | null
  hub: MapPoint | null
  trail: Sighting[]
  now: number
  selected: MapSelection | null
  onSelect: (selection: MapSelection | null) => void
}

/** Distinct consecutive places in a user's trail, oldest first. */
export function trailHops(trail: Sighting[]): { lat: number; lon: number }[] {
  const hops: { lat: number; lon: number }[] = []
  for (const s of trail) {
    if (s.lat == null || s.lon == null) continue
    const last = hops[hops.length - 1]
    if (!last || last.lat !== s.lat || last.lon !== s.lon) hops.push({ lat: s.lat, lon: s.lon })
  }
  return hops
}

export function recent(place: PlaceGroup, now: number): boolean {
  return now / 1000 - place.last_seen < 86400
}
