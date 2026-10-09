import { Crosshair, Home, Maximize2, Minimize2, Minus, Plus } from 'lucide-react'
import {
  addProtocol,
  AttributionControl,
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibre,
  type MapGeoJSONFeature,
  Popup,
  setWorkerUrl,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { Protocol } from 'pmtiles'
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Tileset, type Watching } from '../api'
import { along, arc, circle, distanceKm, type LngLat, measure } from './geometry'
import { buildStyle } from './style'
import { type MapViewProps, placeKey, recent, streamKey, trailHops } from './types'

// The worker loads from this origin (the page's CSP allows nothing else), and
// tiles come from Executor's own archives through the pmtiles protocol.
setWorkerUrl(workerUrl)
const protocol = new Protocol({ metadata: true })
addProtocol('pmtiles', protocol.tile)

type FC = GeoJSON.FeatureCollection
const collection = (features: GeoJSON.Feature[]): FC => ({ type: 'FeatureCollection', features })
const point = (c: LngLat, properties: Record<string, unknown> = {}): GeoJSON.Feature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: c },
  properties,
})
const line = (coords: LngLat[], properties: Record<string, unknown> = {}): GeoJSON.Feature => ({
  type: 'Feature',
  geometry: { type: 'LineString', coordinates: coords },
  properties,
})

interface Leg {
  line: LngLat[]
  lengths: number[]
  kind: 'route' | 'link' | 'threat'
  dots: number
  period: number
}

interface ViewerGroup {
  at: LngLat
  streams: Watching[]
  radius: number | null
  place: string
}

function placeLabel(p: { city?: string | null; country_code?: string | null } | null | undefined): string {
  if (!p) return ''
  return [p.city, p.country_code].filter(Boolean).join(', ')
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  return reduced
}

/** Tooltip content, built from text nodes (never HTML from the data). */
function tip(lines: { text: string; cls?: string }[]): HTMLElement {
  const box = document.createElement('div')
  box.className = 'map-tip'
  for (const l of lines) {
    const row = document.createElement('div')
    if (l.cls) row.className = l.cls
    row.textContent = l.text
    box.appendChild(row)
  }
  return box
}

const INTERACTIVE = [
  'viewer-dot',
  'link-line',
  'route-line',
  'node-core',
  'node-ring',
  'places-dot',
  'places-cluster',
  'trail-dots',
  'threat-dot',
]

export default function MapView(props: MapViewProps & { overlay?: ReactNode; className?: string }) {
  const { variant, mode, origin, hub, nodeStatus, live, places, trail, replay, selected, tour } = props
  const threats = useMemo(() => (mode === 'threats' ? (props.threats ?? []) : []), [mode, props.threats])
  const wrap = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibre | null>(null)
  const [ready, setReady] = useState(false)
  const [tiles, setTiles] = useState<Tileset[] | null>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const legs = useRef<Leg[]>([])
  const refit = useRef<(animate: boolean) => void>(() => {})
  const userMoved = useRef(false)
  const onOpen = useRef(props.onOpen)
  onOpen.current = props.onOpen
  const reduced = useReducedMotion()
  const groupsRef = useRef<ViewerGroup[]>([])

  useEffect(() => {
    let stopped = false
    api
      .tilesets()
      .then((r) => !stopped && setTiles(r.tilesets))
      .catch(() => !stopped && setTiles([]))
    return () => {
      stopped = true
    }
  }, [])

  // --- the map itself, created once the tile archives are known ------------
  useEffect(() => {
    if (!tiles || !box.current) return
    const touch = window.matchMedia('(pointer: coarse)').matches
    const map = new MapLibre({
      container: box.current,
      style: buildStyle(tiles),
      center: [77.5, 14],
      zoom: variant === 'hero' ? 4 : 3,
      minZoom: 1.2,
      maxZoom: 16,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      cooperativeGestures: touch,
      fadeDuration: 150,
    })
    map.touchZoomRotate.disableRotation()
    map.keyboard.disableRotation()
    map.addControl(new AttributionControl({ compact: true }), 'bottom-right')
    map.on('load', () => setReady(true))
    map.on('movestart', (e) => {
      if ((e as { originalEvent?: Event }).originalEvent) userMoved.current = true
    })

    const popup = new Popup({ closeButton: false, closeOnClick: false, offset: 14, className: 'map-pop', maxWidth: '260px' })
    const layersHere = () => INTERACTIVE.filter((id) => map.getLayer(id))
    const pick = (x: number, y: number, pad: number): MapGeoJSONFeature | undefined =>
      map.queryRenderedFeatures(
        [
          [x - pad, y - pad],
          [x + pad, y + pad],
        ],
        { layers: layersHere() },
      )[0]

    const describe = (f: MapGeoJSONFeature): HTMLElement => {
      const p = f.properties as Record<string, string | number | boolean>
      switch (f.layer.id) {
        case 'viewer-dot':
        case 'link-line': {
          const group = groupsRef.current[Number(p.group)]
          if (!group) return tip([{ text: 'Viewer' }])
          return tip([
            ...group.streams.slice(0, 4).flatMap((s) => [
              { text: s.user, cls: 'tip-strong user-name' },
              { text: `${s.paused ? 'Paused · ' : ''}${s.title}`, cls: 'tip-muted' },
            ]),
            ...(group.streams.length > 4 ? [{ text: `and ${group.streams.length - 4} more`, cls: 'tip-muted' }] : []),
            { text: group.place || 'Unknown place', cls: 'tip-place' },
          ])
        }
        case 'route-line':
          return tip([
            { text: `${p.from} → ${p.to}`, cls: 'tip-strong' },
            { text: `${p.count} ${Number(p.count) === 1 ? 'stream' : 'streams'} on this route`, cls: 'tip-muted' },
          ])
        case 'node-core':
        case 'node-ring':
          return tip([
            { text: String(p.label), cls: 'tip-strong' },
            { text: p.role === 'origin' ? 'Media server' : 'Relay to viewers', cls: 'tip-muted' },
            { text: p.machine ? `Machine ${p.status}` : '', cls: 'tip-muted' },
          ])
        case 'places-cluster':
          return tip([{ text: `${p.point_count} places`, cls: 'tip-strong' }, { text: 'Click to zoom in', cls: 'tip-muted' }])
        case 'places-dot':
          return tip([
            { text: String(p.city || 'Unknown place'), cls: 'tip-strong' },
            { text: `${p.count} sessions · ${p.users} ${Number(p.users) === 1 ? 'user' : 'users'}`, cls: 'tip-muted' },
          ])
        case 'trail-dots':
          return tip([{ text: `${p.n}. ${p.place}`, cls: 'tip-strong' }, { text: String(p.when), cls: 'tip-muted' }])
        case 'threat-dot':
          return tip([
            { text: String(p.label || 'Unknown place'), cls: 'tip-strong' },
            { text: `${Number(p.count).toLocaleString()} attempts`, cls: 'tip-muted' },
          ])
        default:
          return tip([])
      }
    }

    map.on('mousemove', (e) => {
      const f = pick(e.point.x, e.point.y, 3)
      map.getCanvas().style.cursor = f ? 'pointer' : ''
      if (!f || touch) {
        popup.remove()
        return
      }
      popup.setLngLat(e.lngLat).setDOMContent(describe(f)).addTo(map)
    })
    map.on('mouseout', () => popup.remove())
    map.on('click', async (e) => {
      const f = pick(e.point.x, e.point.y, touch ? 12 : 4)
      if (!f) return
      popup.remove()
      const p = f.properties as Record<string, string | number>
      switch (f.layer.id) {
        case 'viewer-dot':
        case 'link-line': {
          const group = groupsRef.current[Number(p.group)]
          if (!group) return
          if (group.streams.length === 1) onOpen.current('stream', streamKey(group.streams[0]))
          else onOpen.current('place', `${group.at[1]},${group.at[0]}`)
          return
        }
        case 'route-line':
          onOpen.current('route', '')
          return
        case 'node-core':
        case 'node-ring':
          if (p.machine) onOpen.current('machine', String(p.machine))
          else onOpen.current('route', '')
          return
        case 'places-cluster': {
          const source = map.getSource('places') as GeoJSONSource
          const zoom = await source.getClusterExpansionZoom(Number(p.cluster_id))
          const geometry = f.geometry as GeoJSON.Point
          userMoved.current = true
          map.easeTo({ center: geometry.coordinates as LngLat, zoom: zoom + 0.3 })
          return
        }
        case 'places-dot':
          onOpen.current('place', String(p.key))
          return
        case 'trail-dots':
          onOpen.current('place', String(p.key))
          return
      }
    })

    const resize = new ResizeObserver(() => {
      map.resize()
      // A map laid out after it loaded still frames everything, unless someone moved it.
      if (!userMoved.current) refit.current(false)
    })
    resize.observe(box.current)
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __map: MapLibre }).__map = map
    return () => {
      resize.disconnect()
      popup.remove()
      map.remove()
      mapRef.current = null
      setReady(false)
    }
  }, [tiles, variant])

  // --- data ------------------------------------------------------------------
  const groups = useMemo<ViewerGroup[]>(() => {
    const byPlace = new Map<string, ViewerGroup>()
    for (const s of live) {
      const loc = s.location
      if (!loc || loc.lat == null) continue
      const key = `${loc.lat},${loc.lon}`
      const group = byPlace.get(key) ?? { at: [loc.lon, loc.lat] as LngLat, streams: [], radius: null, place: placeLabel(loc) }
      group.streams.push(s)
      group.radius = Math.max(group.radius ?? 0, loc.radius_km ?? 0) || null
      byPlace.set(key, group)
    }
    return [...byPlace.values()]
  }, [live])
  groupsRef.current = groups

  const hops = useMemo(() => trailHops(trail), [trail])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const set = (name: string, data: FC) => (map.getSource(name) as GeoJSONSource | undefined)?.setData(data)
    const o: LngLat | null = origin ? [origin.lon, origin.lat] : null
    const h: LngLat | null = hub ? [hub.lon, hub.lat] : null

    // Anchors: home (the media server) and the relay, ringed by their machine's health.
    const nodes: GeoJSON.Feature[] = []
    if (o && origin) {
      nodes.push(point(o, { role: 'origin', label: origin.label, machine: origin.machine ?? '', status: nodeStatus[origin.machine ?? ''] ?? 'unknown' }))
    }
    if (h && hub) {
      nodes.push(point(h, { role: 'hub', label: hub.label, machine: hub.machine ?? '', status: nodeStatus[hub.machine ?? ''] ?? 'unknown' }))
    }
    set('nodes', collection(nodes))

    const next: Leg[] = []
    const routeLine = o && h ? arc(o, h, 0.25) : null
    set(
      'route',
      collection(routeLine ? [line(routeLine, { from: origin?.label, to: hub?.label, count: live.length })] : []),
    )
    if (routeLine) next.push({ line: routeLine, lengths: measure(routeLine), kind: 'route', dots: 3, period: 3600 })

    // Viewers, grouped by place, with a link from the relay to each.
    const from = h ?? o
    const crowd: LngLat | undefined = o ?? undefined
    const links: GeoJSON.Feature[] = []
    const viewers: GeoJSON.Feature[] = []
    const accuracy: GeoJSON.Feature[] = []
    groups.forEach((g, i) => {
      const keys = g.streams.map(streamKey)
      const isSelected = !!selected && keys.includes(selected)
      const paused = g.streams.every((s) => s.paused)
      viewers.push(
        point(g.at, {
          group: i,
          paused,
          selected: isSelected,
          place: g.streams.length > 1 ? `${g.place} ×${g.streams.length}` : g.place,
        }),
      )
      if (from && distanceKm(from, g.at) > 15) {
        const path = arc(from, g.at, 0.2, crowd)
        links.push(line(path, { group: i, paused, selected: isSelected }))
        if (!paused) next.push({ line: path, lengths: measure(path), kind: 'link', dots: 2, period: 2800 })
      }
      if (isSelected && g.radius && g.radius >= 10) {
        accuracy.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [circle(g.at, g.radius)] }, properties: {} })
      }
    })
    set('viewers', collection(viewers))
    set('links', collection(links))
    set('accuracy', collection(accuracy))
    legs.current = next

    // All places seen (or one user's), busiest ones named.
    const showPlaces = mode === 'all' || trail.length > 0
    const named = new Set(
      [...places]
        .sort((a, b) => b.count - a.count)
        .slice(0, 14)
        .map(placeKey),
    )
    const now = Date.now()
    set(
      'places',
      collection(
        showPlaces
          ? places.map((p) =>
              point([p.lon, p.lat], {
                key: placeKey(p),
                city: placeLabel(p),
                count: p.count,
                users: p.users.length,
                recent: recent(p, now),
                label: named.has(placeKey(p)) ? (p.city ?? '') : '',
              }),
            )
          : [],
      ),
    )

    // One user's path, numbered in order.
    set('trail', collection(hops.length > 1 ? [line(hops.map((x) => [x.lon, x.lat] as LngLat))] : []))
    set(
      'trail-points',
      collection(
        hops.map((x, i) =>
          point([x.lon, x.lat], {
            n: i + 1,
            current: replay === i,
            key: placeKey(x),
            place: placeLabel(x.sighting) || 'Unknown place',
            when: new Date(x.sighting.first_seen * 1000).toLocaleString(undefined, {
              day: 'numeric',
              month: 'short',
              hour: '2-digit',
              minute: '2-digit',
            }),
          }),
        ),
      ),
    )
    set('replay', collection(replay != null && hops[replay] ? [point([hops[replay].lon, hops[replay].lat])] : []))

    // Attack origins, converging on the relay: the busiest named, the busiest few flowing.
    const target = h ?? o
    const namedThreats = new Set(threats.slice(0, 10).map((t) => t.key))
    set('threats', collection(threats.map((t) => point([t.lon, t.lat], { ...t, named: namedThreats.has(t.key) }))))
    const threatLinks: GeoJSON.Feature[] = []
    if (target) {
      threats.slice(0, 120).forEach((t, i) => {
        const from: LngLat = [t.lon, t.lat]
        if (distanceKm(from, target) < 15) return
        const path = arc(from, target, 0.18)
        threatLinks.push(line(path, { count: t.count }))
        if (i < 40) next.push({ line: path, lengths: measure(path), kind: 'threat', dots: 1, period: 2400 + (i % 7) * 260 })
      })
    }
    set('threat-links', collection(threatLinks))
  }, [ready, origin, hub, nodeStatus, groups, live.length, places, hops, mode, trail.length, replay, selected, threats])

  // --- flowing dots along the route and every link ----------------------------
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const flow = map.getSource('flow') as GeoJSONSource | undefined
    if (!flow) return
    const frame = (t: number) => {
      const features: GeoJSON.Feature[] = []
      for (const leg of legs.current) {
        for (let i = 0; i < leg.dots; i += 1) {
          const k = reduced ? (i + 0.5) / leg.dots : (t / leg.period + i / leg.dots) % 1
          features.push(point(along(leg.line, leg.lengths, k), { leg: leg.kind }))
        }
      }
      flow.setData(collection(features))
      if (map.getLayer('viewer-halo') && !reduced) {
        const phase = (t % 2000) / 2000
        map.setPaintProperty('viewer-halo', 'circle-radius', 8 + phase * 16)
        map.setPaintProperty('viewer-halo', 'circle-opacity', 0.32 * (1 - phase))
      }
    }
    if (reduced) {
      frame(0)
      return
    }
    let id = 0
    let last = 0
    const loop = (t: number) => {
      // About 30 frames a second is plenty for small dots, and skip work in hidden tabs.
      if (t - last > 33 && !document.hidden) {
        frame(t)
        last = t
      }
      id = requestAnimationFrame(loop)
    }
    id = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(id)
  }, [ready, reduced])

  // --- camera ------------------------------------------------------------------
  const focusPoints = useCallback((): LngLat[] => {
    const pts: LngLat[] = []
    if (hops.length) return hops.map((x) => [x.lon, x.lat])
    if (origin) pts.push([origin.lon, origin.lat])
    if (hub) pts.push([hub.lon, hub.lat])
    if (mode === 'threats') {
      // The relay and its attackers; home is not part of this picture.
      pts.length = 0
      if (hub) pts.push([hub.lon, hub.lat])
      threats.forEach((t) => pts.push([t.lon, t.lat]))
      return pts
    }
    if (mode === 'all') places.forEach((p) => pts.push([p.lon, p.lat]))
    else groups.forEach((g) => pts.push(g.at))
    return pts
  }, [hops, origin, hub, mode, places, groups, threats])

  const fit = useCallback(
    (animate = true) => {
      const map = mapRef.current
      const pts = focusPoints()
      if (!map || !pts.length) return
      const bounds = pts.reduce((b, p) => b.extend(p), new LngLatBounds(pts[0], pts[0]))
      const el = box.current
      const narrow = (el?.clientWidth ?? 800) < 600
      const pad = narrow ? 36 : 70
      map.fitBounds(bounds, {
        // Room for the overlays: the live badge (hero) or the controls (explorer) at the top.
        padding: { top: pad + (narrow ? 40 : variant === 'hero' ? 60 : 50), bottom: pad + 20, left: pad + 10, right: pad + 30 },
        maxZoom: hops.length ? 9 : 6.5,
        duration: animate ? 1200 : 0,
      })
      userMoved.current = false
    },
    [focusPoints, variant, hops.length],
  )

  refit.current = fit

  const fitKey = `${mode}|${hops.length ? trail[0]?.user_id : ''}|${hops.length}|${
    mode === 'threats' ? threats.length : mode === 'all' ? places.length : groups.map((g) => g.at.join(',')).join(';')
  }`
  const fitted = useRef('')
  useEffect(() => {
    if (!ready) return
    if (fitted.current === fitKey) return
    const first = fitted.current === ''
    fitted.current = fitKey
    if (!userMoved.current || first) fit(!first)
  }, [ready, fitKey, fit])

  // Attract mode: drift from viewer to viewer, then back out.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !tour) return
    const stops = groups.length ? groups.map((g) => g.at) : places.slice(0, 6).map((p) => [p.lon, p.lat] as LngLat)
    let i = 0
    const id = window.setInterval(() => {
      if (!stops.length) return
      if (i % (stops.length + 1) === stops.length) fit(true)
      else map.flyTo({ center: stops[i % (stops.length + 1)], zoom: 6.5, speed: 0.6, curve: 1.6 })
      i += 1
    }, 6500)
    return () => window.clearInterval(id)
  }, [ready, tour, groups, places, fit])

  useEffect(() => {
    const onChange = () => {
      setFullscreen(document.fullscreenElement === wrap.current)
      window.setTimeout(() => mapRef.current?.resize(), 50)
    }
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void wrap.current?.requestFullscreen?.()
  }

  return (
    <div className={`mapview mapview-${variant} ${props.className ?? ''}`} ref={wrap}>
      <div className="mapview-canvas" ref={box} />
      {tiles && tiles.length === 0 && (
        <p className="map-notice small">Map tiles are not installed, so only the overlays show. See the README.</p>
      )}
      <div className="map-controls">
        <button type="button" onClick={() => mapRef.current?.zoomIn()} aria-label="Zoom in" title="Zoom in">
          <Plus size={16} />
        </button>
        <button type="button" onClick={() => mapRef.current?.zoomOut()} aria-label="Zoom out" title="Zoom out">
          <Minus size={16} />
        </button>
        <button type="button" onClick={() => fit(true)} aria-label="Fit everything in view" title="Fit everything in view">
          <Crosshair size={16} />
        </button>
        {origin && (
          <button
            type="button"
            onClick={() => {
              userMoved.current = true
              mapRef.current?.flyTo({ center: [origin.lon, origin.lat], zoom: 9, speed: 0.9 })
            }}
            aria-label={`Fly to ${origin.label}`}
            title={`Fly to ${origin.label}`}
          >
            <Home size={16} />
          </button>
        )}
        <button
          type="button"
          onClick={toggleFullscreen}
          aria-label={fullscreen ? 'Leave full screen' : 'Full screen'}
          title={fullscreen ? 'Leave full screen' : 'Full screen'}
        >
          {fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
      </div>
      {props.overlay}
    </div>
  )
}
