import { type Flavor, layers, namedFlavor } from '@protomaps/basemaps'
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import type { Tileset } from '../api'

// A dark, Google-like basemap tinted to the page's navy, drawn from the
// Protomaps archives Executor serves itself. The world archive comes first;
// detail archives (a region at street level) draw on top where they have tiles.

const FLAVOR: Flavor = {
  ...namedFlavor('dark'),
  background: '#0a111d',
  earth: '#101a2a',
  park_a: '#12251f',
  park_b: '#132a21',
  hospital: '#1a1d2d',
  industrial: '#131b2a',
  school: '#161d2d',
  wood_a: '#11231c',
  wood_b: '#11241d',
  pedestrian: '#141d2d',
  scrub_a: '#13221e',
  scrub_b: '#13221e',
  glacier: '#1b2536',
  sand: '#1a202c',
  beach: '#1c2230',
  aerodrome: '#151f33',
  runway: '#1f2a40',
  water: '#08182b',
  zoo: '#14231e',
  military: '#191e2b',
  pier: '#1d283b',
  buildings: '#0d1625',
  tunnel_other_casing: '#0a111d',
  tunnel_minor_casing: '#0a111d',
  tunnel_link_casing: '#0a111d',
  tunnel_major_casing: '#0a111d',
  tunnel_highway_casing: '#0a111d',
  tunnel_other: '#18233a',
  tunnel_minor: '#18233a',
  tunnel_link: '#1d2a43',
  tunnel_major: '#1d2a43',
  tunnel_highway: '#3a3426',
  minor_service_casing: '#0c1422',
  minor_casing: '#0c1422',
  link_casing: '#0c1422',
  major_casing_late: '#0c1422',
  highway_casing_late: '#0c1422',
  other: '#1a2538',
  minor_service: '#1a2538',
  minor_a: '#202d44',
  minor_b: '#1c283d',
  link: '#2a3a55',
  major_casing_early: '#0c1422',
  major: '#2b3b58',
  highway_casing_early: '#0c1422',
  highway: '#5b4b2b',
  railway: '#25314a',
  boundaries: '#56688a',
  bridges_other_casing: '#0c1422',
  bridges_minor_casing: '#0c1422',
  bridges_link_casing: '#0c1422',
  bridges_major_casing: '#0c1422',
  bridges_highway_casing: '#0c1422',
  bridges_other: '#1a2538',
  bridges_minor: '#202d44',
  bridges_link: '#2a3a55',
  bridges_major: '#2b3b58',
  bridges_highway: '#5b4b2b',
  roads_label_minor: '#6a7891',
  roads_label_minor_halo: '#0a111d',
  roads_label_major: '#8b9ab2',
  roads_label_major_halo: '#0a111d',
  ocean_label: '#3c6895',
  subplace_label: '#7c8aa3',
  subplace_label_halo: '#0a111d',
  city_label: '#c9d5e8',
  city_label_halo: '#0a111d',
  state_label: '#61779b',
  state_label_halo: '#0a111d',
  country_label: '#a3b4cd',
  address_label: '#5b6a84',
  address_label_halo: '#0a111d',
  landcover: {
    grassland: 'rgba(17, 35, 29, 1)',
    barren: 'rgba(22, 28, 40, 1)',
    urban_area: 'rgba(20, 29, 44, 1)',
    farmland: 'rgba(18, 34, 30, 1)',
    glacier: 'rgba(27, 37, 54, 1)',
    scrub: 'rgba(19, 34, 30, 1)',
    forest: 'rgba(16, 36, 30, 1)',
  },
}

export const COLORS = {
  origin: '#a78bfa',
  hub: '#38bdf8',
  live: '#f5a524',
  place: '#7dd3fc',
  trail: '#f472b6',
  up: '#34d399',
  degraded: '#fbbf24',
  down: '#f87171',
  unknown: '#64748b',
  halo: '#0a111d',
  threat: '#f87171',
}

const ATTRIBUTION =
  '<a href="https://protomaps.com" target="_blank" rel="noopener noreferrer">Protomaps</a> © ' +
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'

const FONT = ['Noto Sans Medium']
const FONT_REGULAR = ['Noto Sans Regular']

function basemapLayers(source: string, detail: boolean): LayerSpecification[] {
  const list = layers(source, FLAVOR, { lang: 'en' }) as unknown as LayerSpecification[]
  if (!detail) return list
  // A detail archive repeats every layer for its own area, except the background.
  return list.filter((l) => l.type !== 'background').map((l) => ({ ...l, id: `${source}:${l.id}` }) as LayerSpecification)
}

/** Overlays drawn above the basemap: links, places, viewers, flow, nodes. */
function overlayLayers(): LayerSpecification[] {
  return [
    {
      id: 'acc-fill',
      type: 'fill',
      source: 'accuracy',
      paint: { 'fill-color': COLORS.live, 'fill-opacity': 0.06 },
    },
    {
      id: 'acc-line',
      type: 'line',
      source: 'accuracy',
      paint: { 'line-color': COLORS.live, 'line-opacity': 0.35, 'line-width': 1, 'line-dasharray': [2, 3] },
    },
    {
      id: 'trail-line',
      type: 'line',
      source: 'trail',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': COLORS.trail, 'line-width': 2, 'line-opacity': 0.8, 'line-dasharray': [1.5, 2] },
    },
    {
      id: 'route-glow',
      type: 'line',
      source: 'route',
      layout: { 'line-cap': 'round' },
      paint: { 'line-color': COLORS.hub, 'line-width': 12, 'line-blur': 8, 'line-opacity': 0.22 },
    },
    {
      id: 'route-line',
      type: 'line',
      source: 'route',
      layout: { 'line-cap': 'round' },
      paint: {
        'line-width': 3.5,
        'line-gradient': ['interpolate', ['linear'], ['line-progress'], 0, COLORS.origin, 1, COLORS.hub],
      },
    },
    {
      id: 'link-glow',
      type: 'line',
      source: 'links',
      layout: { 'line-cap': 'round' },
      paint: {
        'line-color': COLORS.live,
        'line-width': ['case', ['boolean', ['get', 'selected'], false], 12, 7],
        'line-blur': 6,
        'line-opacity': ['case', ['get', 'paused'], 0.06, 0.16],
      },
    },
    {
      id: 'link-line',
      type: 'line',
      source: 'links',
      layout: { 'line-cap': 'round' },
      paint: {
        'line-width': ['case', ['boolean', ['get', 'selected'], false], 3.2, 2],
        'line-opacity': ['case', ['get', 'paused'], 0.4, 0.95],
        'line-gradient': ['interpolate', ['linear'], ['line-progress'], 0, COLORS.hub, 1, COLORS.live],
      },
    },
    {
      id: 'places-cluster',
      type: 'circle',
      source: 'places',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': COLORS.place,
        'circle-opacity': 0.22,
        'circle-stroke-color': COLORS.place,
        'circle-stroke-width': 1.5,
        'circle-radius': ['interpolate', ['linear'], ['get', 'point_count'], 2, 13, 20, 22, 80, 30],
      },
    },
    {
      id: 'places-count',
      type: 'symbol',
      source: 'places',
      filter: ['has', 'point_count'],
      layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': FONT, 'text-size': 12 },
      paint: { 'text-color': '#e6edf6' },
    },
    {
      id: 'places-dot',
      type: 'circle',
      source: 'places',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': ['case', ['get', 'recent'], COLORS.live, COLORS.place],
        'circle-opacity': 0.85,
        'circle-stroke-color': COLORS.halo,
        'circle-stroke-width': 1.5,
        'circle-radius': ['interpolate', ['linear'], ['get', 'count'], 1, 4, 10, 7, 60, 11],
      },
    },
    {
      id: 'places-label',
      type: 'symbol',
      source: 'places',
      filter: ['all', ['!', ['has', 'point_count']], ['!=', ['get', 'label'], '']],
      layout: {
        'text-field': ['get', 'label'],
        'text-font': FONT,
        'text-size': 11.5,
        'text-offset': [0, 1.2],
        'text-anchor': 'top',
        'text-optional': true,
      },
      paint: { 'text-color': '#cfe8f7', 'text-halo-color': COLORS.halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'trail-dots',
      type: 'circle',
      source: 'trail-points',
      paint: {
        'circle-color': COLORS.trail,
        'circle-radius': ['case', ['get', 'current'], 8, 5],
        'circle-stroke-color': '#fff',
        'circle-stroke-width': ['case', ['get', 'current'], 2, 0.6],
      },
    },
    {
      id: 'trail-num',
      type: 'symbol',
      source: 'trail-points',
      layout: {
        'text-field': ['to-string', ['get', 'n']],
        'text-font': FONT,
        'text-size': 10,
        'text-offset': [0, -1.3],
        'text-allow-overlap': false,
      },
      paint: { 'text-color': '#fbcfe8', 'text-halo-color': COLORS.halo, 'text-halo-width': 1.2 },
    },
    {
      id: 'viewer-halo',
      type: 'circle',
      source: 'viewers',
      paint: {
        'circle-color': COLORS.live,
        'circle-opacity': 0.18,
        'circle-radius': 14,
        'circle-blur': 0.4,
      },
    },
    {
      id: 'viewer-dot',
      type: 'circle',
      source: 'viewers',
      paint: {
        'circle-color': ['case', ['get', 'paused'], '#a8875a', COLORS.live],
        'circle-radius': ['case', ['boolean', ['get', 'selected'], false], 8, 6],
        'circle-stroke-color': '#fff7e6',
        'circle-stroke-width': ['case', ['boolean', ['get', 'selected'], false], 2.5, 1.5],
      },
    },
    {
      id: 'viewer-label',
      type: 'symbol',
      source: 'viewers',
      layout: {
        'text-field': ['get', 'place'],
        'text-font': FONT,
        'text-size': 11.5,
        'text-offset': [0, 1.3],
        'text-anchor': 'top',
        'text-optional': true,
      },
      paint: { 'text-color': '#ffe2b0', 'text-halo-color': COLORS.halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'threat-link',
      type: 'line',
      source: 'threat-links',
      layout: { 'line-cap': 'round' },
      paint: {
        'line-width': ['interpolate', ['linear'], ['get', 'count'], 1, 0.8, 50, 1.6, 500, 2.6],
        'line-opacity': 0.55,
        'line-gradient': ['interpolate', ['linear'], ['line-progress'], 0, COLORS.threat, 1, 'rgba(56, 189, 248, 0.6)'],
      },
    },
    {
      id: 'threat-dot',
      type: 'circle',
      source: 'threats',
      paint: {
        'circle-color': COLORS.threat,
        'circle-opacity': 0.85,
        'circle-stroke-color': '#2a0b0b',
        'circle-stroke-width': 1.2,
        'circle-radius': ['interpolate', ['linear'], ['get', 'count'], 1, 3.5, 20, 6, 200, 10, 2000, 15],
      },
    },
    {
      id: 'threat-label',
      type: 'symbol',
      source: 'threats',
      filter: ['==', ['get', 'named'], true],
      layout: {
        'text-field': ['get', 'label'],
        'text-font': FONT,
        'text-size': 11,
        'text-offset': [0, 1.2],
        'text-anchor': 'top',
        'text-optional': true,
      },
      paint: { 'text-color': '#fecaca', 'text-halo-color': COLORS.halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'flow',
      type: 'circle',
      source: 'flow',
      paint: {
        'circle-color': ['match', ['get', 'leg'], 'route', '#c4e9ff', 'threat', '#fca5a5', '#ffe2b0'],
        'circle-radius': ['match', ['get', 'leg'], 'route', 3.4, 'threat', 2.2, 2.8],
        'circle-blur': 0.3,
      },
    },
    {
      id: 'node-ring',
      type: 'circle',
      source: 'nodes',
      paint: {
        'circle-radius': 15,
        'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-width': 2.5,
        'circle-stroke-color': [
          'match',
          ['get', 'status'],
          'up',
          COLORS.up,
          'degraded',
          COLORS.degraded,
          'down',
          COLORS.down,
          COLORS.unknown,
        ],
        'circle-stroke-opacity': 0.9,
      },
    },
    {
      id: 'node-core',
      type: 'circle',
      source: 'nodes',
      paint: {
        'circle-radius': 8,
        'circle-color': ['match', ['get', 'role'], 'origin', COLORS.origin, COLORS.hub],
        'circle-stroke-color': '#0a111d',
        'circle-stroke-width': 2,
      },
    },
    {
      id: 'node-label',
      type: 'symbol',
      source: 'nodes',
      layout: {
        'text-field': ['get', 'label'],
        'text-font': FONT,
        'text-size': 12.5,
        // The relay's name sits above its marker and home's below, so the two never collide.
        'text-offset': ['match', ['get', 'role'], 'hub', ['literal', [0, -1.7]], ['literal', [0, 1.7]]],
        'text-anchor': ['match', ['get', 'role'], 'hub', 'bottom', 'top'],
        'text-allow-overlap': true,
        'text-letter-spacing': 0.05,
      },
      paint: { 'text-color': '#e6edf6', 'text-halo-color': COLORS.halo, 'text-halo-width': 1.6 },
    },
    {
      id: 'replay-dot',
      type: 'circle',
      source: 'replay',
      paint: {
        'circle-radius': 9,
        'circle-color': COLORS.trail,
        'circle-opacity': 0.35,
        'circle-stroke-color': COLORS.trail,
        'circle-stroke-width': 2,
      },
    },
  ] as LayerSpecification[]
}

const EMPTY = { type: 'FeatureCollection', features: [] } as GeoJSON.FeatureCollection

export function buildStyle(tilesets: Tileset[]): StyleSpecification {
  const origin = window.location.origin
  const sources: StyleSpecification['sources'] = {}
  let base: LayerSpecification[] = [
    { id: 'background', type: 'background', paint: { 'background-color': FLAVOR.background } },
  ]
  const details: LayerSpecification[] = []
  tilesets.forEach((t, i) => {
    sources[t.name] = { type: 'vector', url: `pmtiles://${origin}${t.url}`, attribution: ATTRIBUTION }
    if (i === 0) base = basemapLayers(t.name, false)
    else details.push(...basemapLayers(t.name, true))
  })
  for (const name of ['accuracy', 'trail', 'route', 'trail-points', 'viewers', 'flow', 'nodes', 'replay', 'threats']) {
    sources[name] = { type: 'geojson', data: EMPTY }
  }
  sources.links = { type: 'geojson', data: EMPTY, lineMetrics: true }
  sources['threat-links'] = { type: 'geojson', data: EMPTY, lineMetrics: true }
  sources.route = { type: 'geojson', data: EMPTY, lineMetrics: true }
  sources.places = { type: 'geojson', data: EMPTY, cluster: true, clusterRadius: 38, clusterMaxZoom: 9 }
  return {
    version: 8,
    glyphs: `${origin}/map/fonts/{fontstack}/{range}.pbf`,
    sprite: `${origin}/map/sprites/dark`,
    sources,
    layers: [...base, ...details, ...overlayLayers()],
  }
}

export const FONTS = { label: FONT, regular: FONT_REGULAR }
