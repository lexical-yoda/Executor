import {
  ChevronDown,
  Globe2,
  Map as MapIcon,
  MapPin,
  Search,
  X,
} from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type JellyfinStatus,
  type MediaUser,
  type PlaceGroup,
  type Sighting,
} from "../api";
import { ago } from "../format";
import { placeName } from "./NowPlaying";

const GlobeView = lazy(() => import("../map/GlobeView"));
const FlatMap = lazy(() => import("../map/FlatMap"));

const RANGES = [7, 30, 90] as const;

function useNarrow(query = "(max-width: 760px)"): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return narrow;
}

/** Load on mount, when the inputs change, and then on an interval. */
function useLoad<T>(
  load: (() => Promise<T>) | null,
  deps: unknown[],
  intervalMs: number,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!load) {
      setData(null);
      return;
    }
    let stopped = false;
    const run = async () => {
      try {
        const value = await load();
        if (!stopped) {
          setData(value);
          setError(null);
        }
      } catch (err) {
        if (!stopped)
          setError(err instanceof Error ? err.message : String(err));
      }
    };
    void run();
    const id = window.setInterval(
      () => !document.hidden && void run(),
      intervalMs,
    );
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}

function UserPicker({
  users,
  selected,
  onChange,
}: {
  users: MediaUser[];
  selected: string | null;
  onChange: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const current = users.find((u) => u.id === selected);
  const shown = users.filter((u) =>
    u.name.toLowerCase().includes(query.toLowerCase()),
  );

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="picker" ref={box}>
      <button
        type="button"
        className="picker-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {current ? (
          <span className="user-name">{current.name}</span>
        ) : (
          <span>All users</span>
        )}
        <ChevronDown size={14} />
      </button>
      {current && (
        <button
          type="button"
          className="icon-btn picker-clear"
          onClick={() => onChange(null)}
          aria-label="Show all users"
        >
          <X size={14} />
        </button>
      )}
      {open && (
        <div className="picker-menu" role="listbox">
          <label className="picker-search">
            <Search size={13} />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a user"
            />
          </label>
          <button
            type="button"
            className={`picker-item${selected ? "" : " picker-active"}`}
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
          >
            All users
          </button>
          {shown.map((u) => (
            <button
              type="button"
              key={u.id}
              className={`picker-item${u.id === selected ? " picker-active" : ""}`}
              onClick={() => {
                onChange(u.id);
                setOpen(false);
              }}
            >
              <span className="user-name">{u.name}</span>
              <span className="small muted">
                {u.places} {u.places === 1 ? "place" : "places"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function day(seconds: number): string {
  return new Date(seconds * 1000).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

function time(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function Timeline({ sightings }: { sightings: Sighting[] }) {
  const newest = [...sightings].reverse();
  let lastDay = "";
  return (
    <ol className="timeline">
      {newest.map((s) => {
        const d = day(s.first_seen);
        const heading = d !== lastDay ? d : null;
        lastDay = d;
        return (
          <li key={s.id}>
            {heading && <span className="timeline-day">{heading}</span>}
            <div className="timeline-row">
              <span className="timeline-time num">
                {time(s.first_seen)}
                {s.last_seen - s.first_seen > 300
                  ? `–${time(s.last_seen)}`
                  : ""}
              </span>
              <span className="timeline-place">
                <MapPin size={11} /> {placeName(s)}
              </span>
              <span className="small muted">
                {/* Device names often contain a person's name, so they blur with usernames. */}
                {s.device && <span className="user-name">{s.device}</span>}
                {s.device && s.item ? " · " : ""}
                {s.item} <span className="mono">{s.ip}</span>
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function PlaceDetails({
  place,
  now,
  onUser,
}: {
  place: PlaceGroup;
  now: number;
  onUser: (id: string) => void;
}) {
  return (
    <div className="place-details">
      <h4>
        <MapPin size={14} /> {placeName(place)}
        {place.region && <span className="muted small"> · {place.region}</span>}
      </h4>
      <p className="small muted">
        {place.count} sessions · first {ago(place.first_seen * 1000, now)} ·
        last {ago(place.last_seen * 1000, now)}
      </p>
      <ul className="place-users">
        {place.users.map((u) => (
          <li key={u.id}>
            <button
              type="button"
              className="link-btn"
              onClick={() => onUser(u.id)}
            >
              <span className="user-name">{u.name}</span>
            </button>
            <span className="small muted">
              {u.count} · {ago(u.last_seen * 1000, now)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function useViewChoice(): ["globe" | "map", (v: "globe" | "map") => void] {
  const [view, setView] = useState<"globe" | "map">(() => {
    try {
      return window.localStorage.getItem("executor.mapView") === "map"
        ? "map"
        : "globe";
    } catch {
      return "globe";
    }
  });
  const choose = (v: "globe" | "map") => {
    setView(v);
    try {
      window.localStorage.setItem("executor.mapView", v);
    } catch {
      /* not remembered, still switched */
    }
  };
  return [view, choose];
}

export function Audience({
  jellyfin,
  now,
}: {
  jellyfin: JellyfinStatus;
  now: number;
}) {
  const narrow = useNarrow();
  const [chosenView, setView] = useViewChoice();
  // Phones always get the light flat map; the globe chunk is never loaded there.
  const view = narrow ? "map" : chosenView;
  const [days, setDays] = useState<number>(30);
  const [user, setUser] = useState<string | null>(null);
  const [selected, setSelected] = useState<PlaceGroup | null>(null);
  const places = useLoad(
    () => api.mediaPlaces(days, user ?? undefined),
    [days, user],
    60_000,
  );
  const users = useLoad(() => api.mediaUsers(90), [], 300_000);
  const trail = useLoad(
    user ? () => api.mediaTrail(user, days) : null,
    [user, days],
    60_000,
  );
  const placeList = places.data?.places ?? [];
  const sightings = trail.data?.sightings ?? [];
  const top = useMemo(
    () => [...placeList].sort((a, b) => b.count - a.count).slice(0, 8),
    [placeList],
  );
  const live = jellyfin.watching.filter(
    (s) => !user || s.user_id?.replace(/-/g, "") === user,
  );
  const chosen = users.data?.users.find((u) => u.id === user);

  const chooseUser = (id: string | null) => {
    setUser(id);
    setSelected(null);
  };

  const mapProps = {
    places: placeList,
    live,
    hub: jellyfin.hub,
    trail: sightings,
    now,
    onSelect: setSelected,
  };

  return (
    <section className="section">
      <div className="section-head">
        <h2>Audience</h2>
        <div className="audience-controls">
          {!narrow && (
            <div className="range-tabs" role="tablist" aria-label="Map style">
              <button
                type="button"
                role="tab"
                aria-selected={view === "globe"}
                className={view === "globe" ? "active" : ""}
                onClick={() => setView("globe")}
                title="Globe"
              >
                <Globe2 size={13} />
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={view === "map"}
                className={view === "map" ? "active" : ""}
                onClick={() => setView("map")}
                title="Flat map"
              >
                <MapIcon size={13} />
              </button>
            </div>
          )}
          <div className="range-tabs" role="tablist">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                role="tab"
                aria-selected={days === r}
                className={days === r ? "active" : ""}
                onClick={() => setDays(r)}
              >
                {r}d
              </button>
            ))}
          </div>
          <UserPicker
            users={users.data?.users ?? []}
            selected={user}
            onChange={chooseUser}
          />
        </div>
      </div>

      <div className="audience-grid">
        <article className="card audience-map">
          <Suspense
            fallback={
              <div className="map-loading">
                <Globe2 size={22} className="spin-slow" />
              </div>
            }
          >
            {view === "map" ? (
              <FlatMap {...mapProps} />
            ) : (
              <GlobeView {...mapProps} />
            )}
          </Suspense>
          <div className="map-legend small muted">
            <span>
              <i className="lg-place" /> places
            </span>
            <span>
              <i className="lg-live" /> watching now
            </span>
            {jellyfin.hub && (
              <span>
                <i className="lg-hub" /> {jellyfin.hub.label}
              </span>
            )}
            {user && (
              <span>
                <i className="lg-trail" /> path
              </span>
            )}
          </div>
        </article>

        <article className="card audience-side">
          {places.error && (
            <p className="small warn-text">
              History unavailable: {places.error}
            </p>
          )}
          {selected ? (
            <>
              <button
                type="button"
                className="link-btn small"
                onClick={() => setSelected(null)}
              >
                ← back
              </button>
              <PlaceDetails place={selected} now={now} onUser={chooseUser} />
            </>
          ) : user ? (
            <>
              <h4 className="side-title">
                <span className="user-name">{chosen?.name ?? "User"}</span>
                <span className="small muted">
                  {" "}
                  · {new Set(sightings.map((s) => placeName(s))).size} places in{" "}
                  {days} days
                </span>
              </h4>
              {sightings.length ? (
                <Timeline sightings={sightings} />
              ) : (
                <p className="small muted">No sightings in this range.</p>
              )}
            </>
          ) : (
            <>
              <h4 className="side-title">
                Top places{" "}
                <span className="small muted">· last {days} days</span>
              </h4>
              <ul className="top-places">
                {top.map((p) => (
                  <li key={`${p.lat},${p.lon}`}>
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => setSelected(p)}
                    >
                      {placeName(p)}
                    </button>
                    <span className="small muted num">
                      {p.users.length} {p.users.length === 1 ? "user" : "users"}{" "}
                      · {p.count}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="small muted">
                {placeList.length} places
                {places.data?.unlocated
                  ? ` · ${places.data.unlocated} sightings without a location`
                  : ""}
              </p>
            </>
          )}
        </article>
      </div>
      <p className="attribution small muted">
        Locations are approximate (city level).{" "}
        <a href="https://db-ip.com" target="_blank" rel="noreferrer noopener">
          IP geolocation by DB-IP
        </a>{" "}
        (CC BY 4.0) · Map: Natural Earth
      </p>
    </section>
  );
}
