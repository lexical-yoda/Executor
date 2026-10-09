import { Cloud, Gamepad2, Laptop, Monitor, Router, Server, Smartphone } from 'lucide-react'
import type { MachineIcon, MachineStatus } from '../api'
import { ago, duration, latency } from '../format'
import { StatusPill } from './StatusDot'

const ICONS: Record<MachineIcon, typeof Server> = {
  server: Server,
  cloud: Cloud,
  laptop: Laptop,
  phone: Smartphone,
  desktop: Monitor,
  gamepad: Gamepad2,
  router: Router,
}

function Bar({ label, value, max = 100, suffix = '%' }: { label: string; value: number; max?: number; suffix?: string }) {
  const pct = Math.min(100, (value / max) * 100)
  const tone = pct > 90 ? 'down' : pct > 70 ? 'degraded' : 'up'
  return (
    <div className="bar">
      <div className="bar-head">
        <span>{label}</span>
        <span className="num">
          {value.toFixed(value < 10 && suffix !== '%' ? 2 : 0)}
          {suffix}
        </span>
      </div>
      <div className="bar-track">
        <div className={`bar-fill bar-${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function MachineCard({ machine, now, index }: { machine: MachineStatus; now: number; index: number }) {
  const Icon = ICONS[machine.icon] ?? Server
  const d = machine.details
  return (
    <article className={`machine card status-${machine.status}`} style={{ ['--i' as string]: index }}>
      <div className="machine-top">
        <div className="machine-icon">
          <Icon size={20} strokeWidth={1.6} />
        </div>
        <StatusPill status={machine.status} />
      </div>
      <h3>{machine.name}</h3>
      <p className="muted small">{machine.role}</p>
      <div className="machine-meta">
        <span className="mono">{machine.address ?? '—'}</span>
        {machine.status === 'up' && machine.latency_ms !== null && (
          <span className="num">{latency(machine.latency_ms)}</span>
        )}
        {machine.status === 'down' && <span className="small muted">{machine.last_seen ? `seen ${ago(machine.last_seen, now)}` : 'not seen yet'}</span>}
      </div>
      {d && (
        <div className="machine-stats">
          {d.load && d.cpus ? <Bar label={`Load (${d.cpus} threads)`} value={d.load[0]} max={d.cpus} suffix="" /> : null}
          {d.mem_used_pct != null && <Bar label={`Memory of ${d.mem_total_gb} GB`} value={d.mem_used_pct} />}
          {d.uptime_s != null && (
            <div className="small muted">
              Up <span className="num">{duration(d.uptime_s)}</span>
            </div>
          )}
        </div>
      )}
    </article>
  )
}

export function Machines({ machines, now }: { machines: MachineStatus[]; now: number }) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>Machines</h2>
        <span className="muted small">
          {machines.filter((m) => m.status === 'up').length} of {machines.length} online
        </span>
      </div>
      <div className="machines">
        {machines.map((m, i) => (
          <MachineCard key={m.id} machine={m} now={now} index={i} />
        ))}
      </div>
    </section>
  )
}
