import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import world from "world-atlas/countries-110m.json";
import type { PlaceGroup, Sighting, Watching } from "../api";

/** Natural Earth 1:110m countries (public domain), bundled so no tiles are fetched. */
export const countries = feature(
  world as unknown as Topology,
  (world as unknown as Topology).objects.countries,
) as unknown as GeoJSON.FeatureCollection;

export interface Hub {
  label: string;
  lat: number;
  lon: number;
}

export interface MapProps {
  places: PlaceGroup[];
  live: Watching[];
  hub: Hub | null;
  trail: Sighting[];
  now: number;
  onSelect: (place: PlaceGroup | null) => void;
}

export const COLORS = {
  land: "rgba(148, 163, 184, 0.30)",
  place: "#f5a524",
  placeOld: "rgba(245, 165, 36, 0.45)",
  live: "#34d399",
  hub: "#38bdf8",
  trail: "#fbbf24",
};

/** Distinct consecutive places in a user's trail, oldest first. */
export function trailHops(trail: Sighting[]): { lat: number; lon: number }[] {
  const hops: { lat: number; lon: number }[] = [];
  for (const s of trail) {
    if (s.lat == null || s.lon == null) continue;
    const last = hops[hops.length - 1];
    if (!last || last.lat !== s.lat || last.lon !== s.lon)
      hops.push({ lat: s.lat, lon: s.lon });
  }
  return hops;
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

export function recent(place: PlaceGroup, now: number): boolean {
  return now / 1000 - place.last_seen < 86400;
}
