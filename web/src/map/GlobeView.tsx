import Globe, { type GlobeInstance } from "globe.gl";
import { useEffect, useRef } from "react";
import type { PlaceGroup, Watching } from "../api";
import { ago } from "../format";
import {
  COLORS,
  countries,
  escapeHtml,
  type MapProps,
  recent,
  trailHops,
} from "./shared";

type Arc = {
  startLat: number;
  startLng: number;
  endLat: number;
  endLng: number;
  kind: "live" | "trail";
};

function placeLabel(p: PlaceGroup, now: number): string {
  const where = [p.city, p.country_code ?? p.country]
    .filter(Boolean)
    .map((s) => escapeHtml(String(s)))
    .join(", ");
  const names = p.users
    .slice(0, 6)
    .map((u) => `<span class="user-name">${escapeHtml(u.name)}</span>`)
    .join(", ");
  const more = p.users.length > 6 ? ` +${p.users.length - 6}` : "";
  return (
    `<div class="geo-tip"><strong>${where || "Unknown"}</strong>` +
    `<span>${p.count} sessions · last ${ago(p.last_seen * 1000, now)}</span>` +
    `<span>${names}${more}</span></div>`
  );
}

export default function GlobeView({
  places,
  live,
  hub,
  trail,
  now,
  onSelect,
}: MapProps) {
  const holder = useRef<HTMLDivElement>(null);
  const globe = useRef<GlobeInstance | null>(null);
  const nowRef = useRef(now);
  nowRef.current = now;
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    const g = new Globe(el, { animateIn: true });
    g.backgroundColor("rgba(0,0,0,0)")
      .showAtmosphere(true)
      .atmosphereColor(COLORS.hub)
      .atmosphereAltitude(0.16)
      .hexPolygonsData(countries.features)
      .hexPolygonResolution(3)
      .hexPolygonMargin(0.55)
      .hexPolygonUseDots(true)
      .hexPolygonColor(() => COLORS.land)
      .pointLat("lat")
      .pointLng("lon")
      .pointRadius((d) =>
        Math.min(0.9, 0.22 + Math.sqrt((d as PlaceGroup).count) * 0.05),
      )
      .pointAltitude(
        (d) => 0.01 + Math.log10(1 + (d as PlaceGroup).count) * 0.045,
      )
      .pointColor((d) =>
        recent(d as PlaceGroup, nowRef.current)
          ? COLORS.place
          : COLORS.placeOld,
      )
      .pointLabel((d) => placeLabel(d as PlaceGroup, nowRef.current))
      .onPointClick((d) => selectRef.current(d as PlaceGroup))
      .onGlobeClick(() => selectRef.current(null))
      .ringLat("lat")
      .ringLng("lon")
      .ringColor(() => (t: number) => `rgba(52, 211, 153, ${1 - t})`)
      .ringMaxRadius(4)
      .ringPropagationSpeed(2.2)
      .ringRepeatPeriod(1100)
      .arcColor((d: object) =>
        (d as Arc).kind === "live"
          ? ["rgba(52,211,153,0.15)", COLORS.live]
          : ["rgba(251,191,36,0.2)", COLORS.trail],
      )
      .arcStroke((d) => ((d as Arc).kind === "live" ? 0.6 : 0.4))
      .arcDashLength(0.45)
      .arcDashGap(0.25)
      .arcDashAnimateTime((d) => ((d as Arc).kind === "live" ? 1600 : 3200))
      .arcAltitudeAutoScale(0.4)
      .labelLat("lat")
      .labelLng("lon")
      .labelText("label")
      .labelSize(0.9)
      .labelDotRadius(0.45)
      .labelColor(() => COLORS.hub)
      .labelResolution(2);
    const material = g.globeMaterial() as unknown as {
      color: { set: (c: string) => void };
    };
    material.color.set("#081020");
    const controls = g.controls() as unknown as {
      autoRotate: boolean;
      autoRotateSpeed: number;
    };
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.35;
    globe.current = g;

    const resize = () => g.width(el.clientWidth).height(el.clientHeight);
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => {
      observer.disconnect();
      g._destructor();
      el.replaceChildren();
      globe.current = null;
    };
  }, []);

  useEffect(() => {
    const g = globe.current;
    if (!g) return;
    const located = live.filter(
      (s): s is Watching & { location: NonNullable<Watching["location"]> } =>
        !!s.location,
    );
    const arcs: Arc[] = [];
    if (hub) {
      for (const s of located) {
        arcs.push({
          startLat: s.location.lat,
          startLng: s.location.lon,
          endLat: hub.lat,
          endLng: hub.lon,
          kind: "live",
        });
      }
    }
    const hops = trailHops(trail);
    for (let i = 1; i < hops.length; i++) {
      arcs.push({
        startLat: hops[i - 1].lat,
        startLng: hops[i - 1].lon,
        endLat: hops[i].lat,
        endLng: hops[i].lon,
        kind: "trail",
      });
    }
    g.pointsData(places)
      .ringsData(
        located.map((s) => ({ lat: s.location.lat, lon: s.location.lon })),
      )
      .arcsData(arcs)
      .labelsData(hub ? [hub] : []);
  }, [places, live, hub, trail]);

  // Look at the hub first, then follow a selected user's latest place.
  useEffect(() => {
    const g = globe.current;
    if (!g) return;
    const hops = trailHops(trail);
    const target = hops.length ? hops[hops.length - 1] : hub;
    if (target)
      g.pointOfView(
        { lat: target.lat, lng: target.lon, altitude: hops.length ? 1.8 : 2.3 },
        1200,
      );
  }, [trail, hub]);

  return <div className="globe-holder" ref={holder} />;
}
