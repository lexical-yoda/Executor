import { geoGraticule10, geoMercator, geoNaturalEarth1, geoPath, type GeoProjection } from 'd3-geo'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { PlaceGroup, Watching } from '../api'
import { countries110 } from './shared'
import { type MapPoint, type MapProps, recent, streamKey, trailHops } from './types'

const W = 960

type Pt = { lat: number; lon: number }
type XY = [number, number]

/** Map units per CSS pixel, so markers and labels keep their on-screen size. */
function useUnitsPerPixel(ref: React.RefObject<SVGSVGElement | null>): number {
  const [k, setK] = useState(1)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => el.clientWidth && setK(W / el.clientWidth)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref])
  return k
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

/** Fit the projection to the points, or to the whole world when they are spread wide. */
function fit(points: Pt[], height: number): { projection: GeoProjection; world: boolean } {
  const pad = 36
  const extent: [XY, XY] = [
    [pad, pad],
    [W - pad, height - pad],
  ]
  if (points.length) {
    const lons = points.map((p) => p.lon)
    const lats = points.map((p) => p.lat)
    let [west, east] = [Math.min(...lons), Math.max(...lons)]
    let [south, north] = [Math.min(...lats), Math.max(...lats)]
    if (east - west < 100 && north - south < 60) {
      // Keep some context around close points, at the map's own aspect.
      const minLon = 12
      const minLat = (minLon * height) / W
      const cx = (west + east) / 2
      const cy = (south + north) / 2
      const lonSpan = Math.max(minLon, (east - west) * 1.3)
      const latSpan = Math.max(minLat, (north - south) * 1.3)
      ;[west, east] = [cx - lonSpan / 2, cx + lonSpan / 2]
      ;[south, north] = [cy - latSpan / 2, cy + latSpan / 2]
      const box: GeoJSON.MultiPoint = {
        type: 'MultiPoint',
        coordinates: [
          [west, south],
          [east, north],
        ],
      }
      return { projection: geoMercator().fitExtent(extent, box), world: false }
    }
  }
  return {
    projection: geoNaturalEarth1().fitExtent(extent, { type: 'Sphere' }),
    world: true,
  }
}

/** A curve between two screen points. It bows upward, or away from `avoid` when given. */
function arc(a: XY, b: XY, bend = 0.28, avoid?: XY): string {
  const [x1, y1] = a
  const [x2, y2] = b
  const dx = x2 - x1
  const dy = y2 - y1
  const length = Math.hypot(dx, dy) || 1
  let nx = -dy / length
  let ny = dx / length
  const mx = (x1 + x2) / 2
  const my = (y1 + y2) / 2
  const flip = avoid
    ? Math.hypot(mx + nx - avoid[0], my + ny - avoid[1]) < Math.hypot(mx - nx - avoid[0], my - ny - avoid[1])
    : ny > 0
  if (flip) {
    nx = -nx
    ny = -ny
  }
  const lift = Math.min(160, length * bend)
  const cx = mx + nx * lift
  const cy = my + ny * lift
  return `M${x1.toFixed(1)},${y1.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`
}

/** Dots travelling along a path, evenly spaced. */
function Flow({
  d,
  count,
  seconds,
  className,
  r,
}: {
  d: string
  count: number
  seconds: number
  className: string
  r: number
}) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <circle key={i} r={r} className={className}>
          <animateMotion
            dur={`${seconds}s`}
            repeatCount="indefinite"
            begin={`-${((seconds * i) / count).toFixed(2)}s`}
            path={d}
          />
        </circle>
      ))}
    </>
  )
}

function Gradient({ id, from, to, colors }: { id: string; from: XY; to: XY; colors: [string, string] }) {
  return (
    <linearGradient id={id} gradientUnits="userSpaceOnUse" x1={from[0]} y1={from[1]} x2={to[0]} y2={to[1]}>
      <stop offset="0%" stopColor={colors[0]} />
      <stop offset="100%" stopColor={colors[1]} />
    </linearGradient>
  )
}

const ORIGIN = '#a78bfa'
const HUB = '#38bdf8'
const LIVE = '#34d399'

export default function FlatMap({ mode, places, live, origin, hub, trail, now, selected, onSelect }: MapProps) {
  const svg = useRef<SVGSVGElement>(null)
  const k = useUnitsPerPixel(svg)
  const reduced = useReducedMotion()
  const narrow = k > 1.6
  const height = narrow ? 720 : 500
  const hops = trailHops(trail)
  const watching = live.filter((s): s is Watching & { location: NonNullable<Watching['location']> } => !!s.location)
  const ends = [origin, hub].filter((p): p is MapPoint => !!p)

  // Live: the route and current viewers (or a chosen user's trail). All: every place.
  const focus: Pt[] =
    mode === 'all'
      ? [...places, ...ends]
      : hops.length
        ? [...hops, ...ends]
        : [...ends, ...watching.map((s) => s.location)]
  const focusKey = focus.map((p) => `${p.lat.toFixed(2)},${p.lon.toFixed(2)}`).join('|')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const { projection, world } = useMemo(() => fit(focus, height), [focusKey, height])

  const [detailed, setDetailed] = useState<GeoJSON.FeatureCollection | null>(null)
  useEffect(() => {
    if (!world && !detailed) void import('./detailed').then((m) => setDetailed(m.countries50))
  }, [world, detailed])

  const path = useMemo(() => geoPath(projection), [projection])
  const land = useMemo(() => path(!world && detailed ? detailed : countries110) ?? '', [path, world, detailed])
  const grid = useMemo(() => path(geoGraticule10()) ?? '', [path])
  const at = (p: Pt): XY => (projection([p.lon, p.lat]) as XY | null) ?? [-999, -999]

  const o = origin ? at(origin) : null
  const h = hub ? at(hub) : null
  const streaming = watching.length > 0
  const viewers = watching.map((s) => at(s.location))
  // Bow the home-to-relay line away from the viewers, and their arcs away from home.
  const crowd: XY | undefined = viewers.length
    ? [viewers.reduce((t, v) => t + v[0], 0) / viewers.length, viewers.reduce((t, v) => t + v[1], 0) / viewers.length]
    : undefined
  const backbone = o && h ? arc(o, h, 0.35, crowd) : null
  const outbound = h
    ? watching.map((s, i) => {
        const end = at(s.location)
        const near = Math.hypot(end[0] - h[0], end[1] - h[1]) < 6 * k
        return {
          s,
          end,
          d: near ? null : arc(h, end, i % 2 ? 0.22 : 0.3, o ?? undefined),
          key: `s${i}`,
          id: streamKey(s),
        }
      })
    : []
  const trailLine =
    hops.length > 1
      ? path({
          type: 'LineString',
          coordinates: hops.map((p) => [p.lon, p.lat]),
        })
      : null

  const text = 11.5 * k
  const labels = !world
  // Name the busiest places beside their dots, skipping any that would crowd a named one.
  const placeLabels: { p: PlaceGroup; x: number; y: number }[] = []
  if (labels) {
    // Named points and their labels (about 140 px wide) are off limits.
    const taken: XY[] = [...viewers]
    for (const node of [o, h]) if (node) taken.push(node, [node[0] + 70 * k, node[1]], [node[0] - 70 * k, node[1]])
    for (const p of [...places].sort((a, b) => b.count - a.count)) {
      const [x, y] = at(p)
      if (x < 0 || y < 0 || x > W || y > height) continue
      if (taken.some(([tx, ty]) => Math.abs(tx - x) < 70 * k && Math.abs(ty - y) < 16 * k)) continue
      placeLabels.push({ p, x, y })
      taken.push([x, y])
      if (placeLabels.length >= 14) break
    }
  }
  const chosenStream = selected?.kind === 'stream' ? selected.key : null
  const routeChosen = selected?.kind === 'route'
  const pickRoute = () => onSelect({ kind: 'route' })
  const where = (s: Watching) => [s.location?.city, s.location?.country_code].filter(Boolean).join(', ')

  return (
    <svg
      ref={svg}
      className="flat-map"
      viewBox={`0 0 ${W} ${height}`}
      role="img"
      aria-label="Map of where media users connect from"
    >
      <defs>
        {o && h && <Gradient id="g-backbone" from={o} to={h} colors={[ORIGIN, HUB]} />}
        {outbound.map(
          ({ end, key }) => h && <Gradient key={key} id={`g-${key}`} from={h} to={end} colors={[HUB, LIVE]} />,
        )}
      </defs>
      <path d={grid} className="map-grid" />
      <path d={land} className="map-land" strokeWidth={0.6 * k} />

      {places.map((p: PlaceGroup) => {
        const [x, y] = at(p)
        const base =
          mode === 'all' ? 2.5 + Math.min(10, Math.sqrt(p.count) * 1.1) : 1.6 + Math.min(4, Math.sqrt(p.count) * 0.4)
        return (
          <circle
            key={`${p.lat},${p.lon}`}
            cx={x}
            cy={y}
            r={base * k}
            className={`map-place${mode === 'live' ? ' map-place-faint' : ''}${recent(p, now) ? ' map-place-recent' : ''}`}
            strokeWidth={k}
            onClick={() => onSelect({ kind: 'place', place: p })}
          >
            <title>{[p.city, p.country_code].filter(Boolean).join(', ')}</title>
          </circle>
        )
      })}

      {labels &&
        placeLabels.map(({ p, x, y }) => (
          <text
            key={`label-${p.lat},${p.lon}`}
            x={x + 6 * k}
            y={y - 5 * k}
            className="map-label map-label-place"
            fontSize={10 * k}
          >
            {p.city ?? p.country}
          </text>
        ))}

      {trailLine && (
        <path d={trailLine} className="map-trail" strokeWidth={1.8 * k} strokeDasharray={`${5 * k} ${4 * k}`} />
      )}

      {backbone && (
        <g>
          <path
            d={backbone}
            className={`flow-line${streaming ? ' flow-line-live' : ''}${routeChosen ? ' flow-line-selected' : ''}`}
            stroke="url(#g-backbone)"
            strokeWidth={(streaming ? 2.4 : 1.6) * (routeChosen ? 1.6 : 1) * k}
          />
          {!reduced && (
            <Flow
              d={backbone}
              count={streaming ? 4 : 1}
              seconds={streaming ? 2.2 : 4.5}
              r={(streaming ? 3.2 : 2.4) * k}
              className="flow-dot flow-dot-backbone"
            />
          )}
          <path d={backbone} className="flow-hit" strokeWidth={16 * k} onClick={pickRoute}>
            <title>
              {origin?.label} to {hub?.label}: {streaming ? `${watching.length} streams` : 'idle'}
            </title>
          </path>
        </g>
      )}

      {outbound.map(({ s, end, d, key, id }) => {
        const chosen = chosenStream === id
        const dimmed = chosenStream !== null && !chosen
        const pick = () => onSelect({ kind: 'stream', key: id })
        const label = `${s.title} · ${where(s) || 'unknown place'}`
        return (
          <g key={key} className={dimmed ? 'flow-dimmed' : ''}>
            {d && (
              <path
                d={d}
                className={`flow-line flow-line-live${chosen ? ' flow-line-selected' : ''}`}
                stroke={`url(#g-${key})`}
                strokeWidth={(chosen ? 3.2 : 2) * k}
              />
            )}
            {d && !reduced && <Flow d={d} count={3} seconds={1.8} r={2.8 * k} className="flow-dot flow-dot-out" />}
            <circle cx={end[0]} cy={end[1]} r={7 * k} className="map-live-pulse" strokeWidth={2 * k} />
            <circle cx={end[0]} cy={end[1]} r={(chosen ? 5.5 : 4) * k} fill={LIVE} />
            {labels && (
              <text x={end[0] + 8 * k} y={end[1] + 4 * k} className="map-label map-label-live" fontSize={text}>
                <tspan className="user-name">{s.user}</tspan>
                <tspan className="map-label-dim"> · {s.location.city ?? s.location.country}</tspan>
              </text>
            )}
            {d && (
              <path d={d} className="flow-hit" strokeWidth={16 * k} onClick={pick}>
                <title>{label}</title>
              </path>
            )}
            <circle cx={end[0]} cy={end[1]} r={12 * k} className="flow-hit-dot" onClick={pick}>
              <title>{label}</title>
            </circle>
          </g>
        )
      })}

      {o && origin && (
        <g className="map-node" onClick={pickRoute}>
          <title>{origin.label}: where the media server is</title>
          <circle cx={o[0]} cy={o[1]} r={9 * k} fill={ORIGIN} opacity={0.18} />
          <circle cx={o[0]} cy={o[1]} r={4.5 * k} fill={ORIGIN} />
          {labels && (
            <text x={o[0] - 10 * k} y={o[1] + 4 * k} textAnchor="end" className="map-label" fontSize={text}>
              {origin.label}
            </text>
          )}
        </g>
      )}
      {h && hub && (
        <g className="map-node" onClick={pickRoute}>
          <title>{hub.label}: the relay every stream goes through</title>
          <rect
            x={h[0] - 5 * k}
            y={h[1] - 5 * k}
            width={10 * k}
            height={10 * k}
            fill={HUB}
            transform={`rotate(45 ${h[0]} ${h[1]})`}
          />
          {labels && (
            <text x={h[0] + 10 * k} y={h[1] - 8 * k} className="map-label" fontSize={text}>
              {hub.label}
            </text>
          )}
        </g>
      )}
    </svg>
  )
}
