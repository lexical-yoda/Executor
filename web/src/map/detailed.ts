import { feature } from 'topojson-client'
import type { Topology } from 'topojson-specification'
import world from 'world-atlas/countries-50m.json'

/** Natural Earth 1:50m countries, loaded only when the map zooms into a region. */
export const countries50 = feature(
  world as unknown as Topology,
  (world as unknown as Topology).objects.countries,
) as unknown as GeoJSON.FeatureCollection
