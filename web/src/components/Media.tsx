import {
  ArrowDown,
  ArrowUp,
  Clapperboard,
  Download,
  Inbox,
  Tv,
} from "lucide-react";
import { useState } from "react";
import type {
  JellyfinStatus,
  Media as MediaData,
  MediaRequest,
  QueueItem,
} from "../api";
import { ago, bytes, duration, rate } from "../format";
import { NowPlaying } from "./NowPlaying";

function seasons(list: number[]): string {
  if (!list.length) return "";
  if (list.length === 1) return `S${list[0]}`;
  const contiguous = list.every((n, i) => i === 0 || n === list[i - 1] + 1);
  return contiguous
    ? `S${list[0]}–${list[list.length - 1]}`
    : `${list.length} seasons`;
}

function Poster({ item }: { item: MediaRequest }) {
  const [failed, setFailed] = useState(false);
  if (!item.has_poster || failed) {
    return (
      <span className="poster poster-empty" aria-hidden="true">
        {item.kind === "tv" ? <Tv size={16} /> : <Clapperboard size={16} />}
      </span>
    );
  }
  return (
    <img
      className="poster"
      src={`/api/media/poster/${item.kind}/${item.tmdb_id}`}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

function RequestRow({
  item,
  now,
  compact,
}: {
  item: MediaRequest;
  now: number;
  compact?: boolean;
}) {
  return (
    <li className={`request${compact ? " request-compact" : ""}`}>
      <Poster item={item} />
      <div className="request-text">
        <span className="request-title">
          {item.title}
          {item.year && <span className="muted"> ({item.year})</span>}
        </span>
        <span className="small muted">
          <span className={`kind-badge kind-${item.kind}`}>
            {item.kind === "tv" ? "TV" : "Movie"}
          </span>
          {item.seasons.length > 0 && (
            <span className="num"> {seasons(item.seasons)}</span>
          )}
          {item.is_4k && <span className="kind-badge">4K</span>}{" "}
          <span className="user-name">{item.requested_by}</span>
          {item.requested_at && ` · ${ago(item.requested_at, now)}`}
        </span>
      </div>
    </li>
  );
}

function RequestsCard({
  data,
  now,
}: {
  data: MediaData["requests"];
  now: number;
}) {
  const c = data.counts;
  return (
    <article className="edge-card card media-card">
      <div className="edge-head">
        <Inbox size={16} />
        <h3>Requests</h3>
      </div>
      {!data.ok && (
        <p className="small warn-text">Jellyseerr unavailable: {data.error}</p>
      )}
      <div className="stat-chips">
        <span className={`stat-chip${c.pending ? " chip-attention" : ""}`}>
          <span className="num">{c.pending ?? "—"}</span> waiting for approval
        </span>
        <span className="stat-chip">
          <span className="num">{c.processing ?? "—"}</span> on the way
        </span>
        <span className="stat-chip">
          <span className="num">{c.available ?? "—"}</span> available
        </span>
      </div>
      {data.pending.length > 0 ? (
        <ul className="requests">
          {data.pending.map((r) => (
            <RequestRow key={r.id} item={r} now={now} />
          ))}
        </ul>
      ) : (
        data.ok && (
          <p className="small muted">Nothing is waiting for approval.</p>
        )
      )}
      {data.processing.length > 0 && (
        <>
          <h4 className="media-sub">Approved, on the way</h4>
          <ul className="requests">
            {data.processing.map((r) => (
              <RequestRow key={r.id} item={r} now={now} compact />
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

function QueueRow({ item }: { item: QueueItem }) {
  const pctDone =
    item.progress !== null ? Math.round(item.progress * 100) : null;
  const active = item.status === "downloading";
  const tone =
    item.health === "error" || item.status === "failed"
      ? "down"
      : item.health === "warning"
        ? "degraded"
        : "up";
  return (
    <li className={`queue-item q-${tone}`}>
      <div className="queue-top">
        <span className="queue-title">
          {item.source === "sonarr" ? (
            <Tv size={13} />
          ) : (
            <Clapperboard size={13} />
          )}
          <span>{item.title}</span>
          {item.subtitle && (
            <span className="muted small">{item.subtitle}</span>
          )}
        </span>
        <span className="queue-meta small muted num">
          {pctDone !== null && `${pctDone}%`}
          {active && item.eta_s ? ` · ${duration(item.eta_s)} left` : ""}
          {item.size ? ` · ${bytes(item.size)}` : ""}
        </span>
      </div>
      <div className="queue-bar">
        <div
          className={`queue-fill${active ? " queue-active" : ""}`}
          style={{ width: `${pctDone ?? 0}%` }}
        />
      </div>
      {(item.message || (!active && item.state)) && (
        <span className={`small ${tone === "up" ? "muted" : "warn-text"}`}>
          {item.message ?? item.state?.replace(/([A-Z])/g, " $1").toLowerCase()}
        </span>
      )}
    </li>
  );
}

function DownloadsCard({ data }: { data: MediaData["downloads"] }) {
  const t = data.torrents;
  const moving = (t.down_bps ?? 0) > 0;
  return (
    <article className="edge-card card media-card">
      <div className="edge-head">
        <Download size={16} />
        <h3>Downloads</h3>
        {t.configured && t.ok && (
          <span className="speeds num">
            <span className={`speed${moving ? " speed-live" : ""}`}>
              <ArrowDown size={13} /> {rate(t.down_bps ?? 0)}
            </span>
            <span className="speed">
              <ArrowUp size={13} /> {rate(t.up_bps ?? 0)}
            </span>
          </span>
        )}
      </div>
      {t.configured && !t.ok && (
        <p className="small warn-text">qBittorrent unavailable: {t.error}</p>
      )}
      {Object.entries(data.errors).map(([source, error]) => (
        <p key={source} className="small warn-text">
          {error}
        </p>
      ))}
      {t.configured && t.ok && (
        <div className="stat-chips">
          <span className="stat-chip">
            <span className="num">{t.downloading}</span> downloading
          </span>
          <span className="stat-chip">
            <span className="num">{t.seeding}</span> seeding
          </span>
          {!!t.stalled && (
            <span className="stat-chip chip-attention">
              <span className="num">{t.stalled}</span> stalled
            </span>
          )}
          {!!t.errored && (
            <span className="stat-chip chip-bad">
              <span className="num">{t.errored}</span> errored
            </span>
          )}
          {t.connection && t.connection !== "connected" && (
            <span className="stat-chip chip-attention">{t.connection}</span>
          )}
        </div>
      )}
      {data.queue.length > 0 ? (
        <ul className="queue">
          {data.queue.map((q) => (
            <QueueRow key={q.id} item={q} />
          ))}
        </ul>
      ) : (
        <p className="small muted">
          {data.sources.length
            ? `Nothing in the ${data.sources.join(" or ")} queue.`
            : "No queues configured."}
        </p>
      )}
    </article>
  );
}

export function Media({
  media,
  jellyfin,
  now,
}: {
  media: MediaData | null;
  jellyfin: JellyfinStatus | null;
  now: number;
}) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>Media</h2>
      </div>
      {jellyfin && <NowPlaying data={jellyfin} />}
      {media && (
        <div className="media-grid">
          {media.requests.configured && (
            <RequestsCard data={media.requests} now={now} />
          )}
          <DownloadsCard data={media.downloads} />
        </div>
      )}
    </section>
  );
}
