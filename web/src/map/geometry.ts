// Shapes for the map overlays, computed in Web Mercator so curves look the
// same on screen at every zoom.

export type LngLat = [number, number]

function toMerc([lon, lat]: LngLat): [number, number] {
  const clamped = Math.max(-85, Math.min(85, lat))
  const rad = (clamped * Math.PI) / 180
  return [(lon + 180) / 360, (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2]
}

function fromMerc([x, y]: [number, number]): LngLat {
  const lon = x * 360 - 180
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI
  return [lon, lat]
}

/**
 * A curve from a to b, sampled into points. It bows to one side by a share of
 * its length; with `avoid`, it bows away from that point (so lines leaving a
 * crowded spot fan out instead of overlapping).
 */
export function arc(a: LngLat, b: LngLat, bend = 0.22, avoid?: LngLat, samples = 48): LngLat[] {
  const [x1, y1] = toMerc(a)
  const [x2, y2] = toMerc(b)
  const dx = x2 - x1
  const dy = y2 - y1
  const length = Math.hypot(dx, dy)
  if (length < 1e-7) return [a, b]
  let nx = -dy / length
  let ny = dx / length
  const mx = (x1 + x2) / 2
  const my = (y1 + y2) / 2
  if (avoid) {
    const [ax, ay] = toMerc(avoid)
    if (Math.hypot(mx + nx - ax, my + ny - ay) < Math.hypot(mx - nx - ax, my - ny - ay)) {
      nx = -nx
      ny = -ny
    }
  } else if (ny > 0) {
    // Bow "up" on screen by default (Mercator y grows southward).
    nx = -nx
    ny = -ny
  }
  const lift = Math.min(0.08, length * bend)
  const cx = mx + nx * lift
  const cy = my + ny * lift
  const points: LngLat[] = []
  for (let i = 0; i <= samples; i += 1) {
    const t = i / samples
    const u = 1 - t
    points.push(fromMerc([u * u * x1 + 2 * u * t * cx + t * t * x2, u * u * y1 + 2 * u * t * cy + t * t * y2]))
  }
  return points
}

/** Cumulative lengths of a polyline (in Mercator units), for moving along it evenly. */
export function measure(line: LngLat[]): number[] {
  const merc = line.map(toMerc)
  const out = [0]
  for (let i = 1; i < merc.length; i += 1) {
    out.push(out[i - 1] + Math.hypot(merc[i][0] - merc[i - 1][0], merc[i][1] - merc[i - 1][1]))
  }
  return out
}

/** The point a share `t` (0 to 1) of the way along a measured polyline. */
export function along(line: LngLat[], lengths: number[], t: number): LngLat {
  const total = lengths[lengths.length - 1]
  if (!total) return line[0]
  const target = t * total
  let i = 1
  while (i < lengths.length - 1 && lengths[i] < target) i += 1
  const span = lengths[i] - lengths[i - 1] || 1
  const k = (target - lengths[i - 1]) / span
  const [a, b] = [line[i - 1], line[i]]
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]
}

/** A circle of `km` radius on the ground, as a polygon ring. */
export function circle(center: LngLat, km: number, steps = 64): LngLat[] {
  const [lon, lat] = center
  const ring: LngLat[] = []
  const dLat = km / 110.574
  const dLon = km / (111.32 * Math.cos((lat * Math.PI) / 180) || 1)
  for (let i = 0; i <= steps; i += 1) {
    const a = (i / steps) * 2 * Math.PI
    ring.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)])
  }
  return ring
}

export function distanceKm(a: LngLat, b: LngLat): number {
  const rad = Math.PI / 180
  const dLat = (b[1] - a[1]) * rad
  const dLon = (b[0] - a[0]) * rad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2
  return 12742 * Math.asin(Math.sqrt(h))
}
