import { geoGraticule10, geoNaturalEarth1, geoPath } from "d3-geo";
import { useMemo } from "react";
import type { PlaceGroup } from "../api";
import { COLORS, countries, type MapProps, recent, trailHops } from "./shared";

const W = 960;
const H = 500;

export default function FlatMap({
  places,
  live,
  hub,
  trail,
  now,
  onSelect,
}: MapProps) {
  const { projection, land, grid } = useMemo(() => {
    const p = geoNaturalEarth1().fitExtent(
      [
        [4, 4],
        [W - 4, H - 4],
      ],
      { type: "Sphere" },
    );
    const path = geoPath(p);
    return {
      projection: p,
      land: path(countries) ?? "",
      grid: path(geoGraticule10()) ?? "",
    };
  }, []);
  const at = (lat: number, lon: number) =>
    projection([lon, lat]) ?? [-100, -100];
  const hops = trailHops(trail);
  const line =
    hops.length > 1
      ? geoPath(projection)({
          type: "LineString",
          coordinates: hops.map((h) => [h.lon, h.lat]),
        })
      : null;

  return (
    <svg
      className="flat-map"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="Map of where media users connect from"
    >
      <path d={grid} className="map-grid" />
      <path d={land} className="map-land" />
      {line && <path d={line} className="map-trail" />}
      {places.map((p: PlaceGroup) => {
        const [x, y] = at(p.lat, p.lon);
        return (
          <circle
            key={`${p.lat},${p.lon}`}
            cx={x}
            cy={y}
            r={2.5 + Math.min(10, Math.sqrt(p.count) * 1.1)}
            fill={recent(p, now) ? COLORS.place : COLORS.placeOld}
            className="map-place"
            onClick={() => onSelect(p)}
          />
        );
      })}
      {live
        .filter((s) => s.location)
        .map((s, i) => {
          const [x, y] = at(s.location!.lat, s.location!.lon);
          return (
            <g key={`live-${i}`}>
              <circle cx={x} cy={y} r={6} className="map-live-pulse" />
              <circle cx={x} cy={y} r={3.5} fill={COLORS.live} />
            </g>
          );
        })}
      {hub &&
        (() => {
          const [x, y] = at(hub.lat, hub.lon);
          return (
            <g>
              <rect
                x={x - 4}
                y={y - 4}
                width={8}
                height={8}
                fill={COLORS.hub}
                transform={`rotate(45 ${x} ${y})`}
              />
            </g>
          );
        })()}
    </svg>
  );
}
