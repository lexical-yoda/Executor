import { Eye, EyeOff } from "lucide-react";
import type { Snapshot } from "../api";
import { ago } from "../format";
import { usePresentation } from "../hooks";

function Meter({
  label,
  value,
  total,
  known = true,
}: {
  label: string;
  value: number;
  total: number;
  known?: boolean;
}) {
  const ratio = known && total ? value / total : 0;
  const tone = !known
    ? "unknown"
    : ratio === 1
      ? "up"
      : ratio >= 0.8
        ? "degraded"
        : "down";
  return (
    <div className={`meter meter-${tone}`}>
      <svg viewBox="0 0 36 36" className="meter-ring" aria-hidden="true">
        <circle cx="18" cy="18" r="15.5" className="meter-track" />
        <circle
          cx="18"
          cy="18"
          r="15.5"
          className="meter-value"
          strokeDasharray={`${(ratio * 97.4).toFixed(1)} 97.4`}
        />
      </svg>
      <div className="meter-text">
        <span className="meter-number">
          {known ? `${value}/${total}` : "—"}
        </span>
        <span className="meter-label">{label}</span>
      </div>
    </div>
  );
}

export function Header({
  snapshot,
  error,
  updatedAt,
  now,
}: {
  snapshot: Snapshot | null;
  error: string | null;
  updatedAt: number | null;
  now: number;
}) {
  const s = snapshot?.summary;
  const down =
    snapshot?.services.filter((x) => x.status === "down").length ?? 0;
  const degraded =
    snapshot?.services.filter((x) => x.status === "degraded").length ?? 0;
  const machinesDown =
    snapshot?.machines.filter((x) => x.status === "down").length ?? 0;

  let headline = "Connecting…";
  let tone = "unknown";
  if (error && !snapshot) {
    headline = "Cannot reach Executor";
    tone = "down";
  } else if (snapshot) {
    if (down) {
      headline = `${down} service${down > 1 ? "s" : ""} down`;
      tone = "down";
    } else if (degraded || machinesDown) {
      const parts = [];
      if (degraded) parts.push(`${degraded} degraded`);
      if (machinesDown)
        parts.push(
          `${machinesDown} machine${machinesDown > 1 ? "s" : ""} offline`,
        );
      headline = parts.join(", ");
      tone = "degraded";
    } else {
      headline = "All systems nominal";
      tone = "up";
    }
  }

  const stale = updatedAt !== null && now - updatedAt > 30_000;
  const [presenting, togglePresenting] = usePresentation();

  return (
    <header className="header">
      <div className="brand">
        <svg viewBox="0 0 32 32" className="brand-mark" aria-hidden="true">
          <path d="M16 3 29 27H3Z" />
          <path d="M10.5 21h11M12.5 16.5h7" />
        </svg>
        <div>
          <h1>{snapshot?.site.title ?? "Executor"}</h1>
          <p className="brand-sub">
            {snapshot?.site.subtitle ?? "Command deck"}
          </p>
        </div>
      </div>

      <div className={`headline headline-${tone}`} aria-live="polite">
        <span className="headline-glow" />
        <span className="headline-text">{headline}</span>
        <span className={`live ${error || stale ? "live-off" : ""}`}>
          <span className="live-dot" />
          {error
            ? "connection lost"
            : updatedAt
              ? `updated ${ago(updatedAt, now)}`
              : "waiting"}
        </span>
      </div>

      <div className="meters">
        <Meter
          label="Services"
          value={s?.services_up ?? 0}
          total={s?.services_total ?? 0}
          known={!!s}
        />
        <Meter
          label="Machines"
          value={s?.machines_up ?? 0}
          total={s?.machines_total ?? 0}
          known={!!s}
        />
        <Meter
          label="Containers"
          value={s?.containers_running ?? 0}
          total={s?.containers_total ?? 0}
          known={!!s?.containers_known}
        />
        <button
          type="button"
          className={`icon-btn present-btn${presenting ? " present-on" : ""}`}
          onClick={togglePresenting}
          aria-pressed={presenting}
          title={
            presenting
              ? "Presentation mode on: usernames are blurred"
              : "Presentation mode: blur usernames"
          }
        >
          {presenting ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
    </header>
  );
}
