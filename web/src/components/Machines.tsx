import {
  Activity,
  ArrowDown,
  ArrowUp,
  Clock,
  Cloud,
  Cpu,
  Gamepad2,
  HardDrive,
  Laptop,
  Monitor,
  Router,
  Server,
  Smartphone,
  Thermometer,
} from 'lucide-react'
import type { ReactNode } from 'react'
import type { MachineIcon, MachineStats, MachineStatus } from '../api'
import { ago, duration, gib, latency, machineState, pct, rate } from '../format'
import { useApp, useClock } from '../state'
import { AttachedActions } from './ActionKit'
import { Sparkline } from './Sparkline'
import { StatusDot, StatusPill } from './StatusDot'
import { Num } from './ui'

export const ICONS: Record<MachineIcon, typeof Server> = {
  server: Server,
  cloud: Cloud,
  laptop: Laptop,
  phone: Smartphone,
  desktop: Monitor,
  gamepad: Gamepad2,
  router: Router,
}

function tone(value: number | null | undefined, warn = 70, bad = 90) {
  if (value == null) return 'unknown'
  return value >= bad ? 'down' : value >= warn ? 'degraded' : 'up'
}

function Bar({ value, className = '' }: { value: number | null; className?: string }) {
  return (
    <div className={`bar-track ${className}`}>
      <div className={`bar-fill bar-${tone(value)}`} style={{ width: `${Math.min(100, value ?? 0)}%` }} />
    </div>
  )
}

function Gauge({ label, value, detail }: { label: string; value: number | null; detail?: string }) {
  return (
    <div className="gauge">
      <div className="gauge-head">
        <span className="gauge-label">{label}</span>
        <Num value={value} format={(v) => pct(v)} className={`gauge-value tone-${tone(value)}`} />
      </div>
      <Bar value={value} />
      {detail && <span className="gauge-detail small muted num">{detail}</span>}
    </div>
  )
}

function Chip({ icon: Icon, children, title }: { icon: typeof Server; children: ReactNode; title: string }) {
  return (
    <span className="stat-chip" title={title}>
      <Icon size={12} aria-hidden="true" />
      <span className="num">{children}</span>
    </span>
  )
}

function Storage({ stats }: { stats: MachineStats }) {
  if (stats.pools.length) {
    return (
      <div className="pools">
        {stats.pools.map((p) => (
          <div key={p.name} className="pool">
            <div className="pool-head">
              <span className="pool-name">{p.name}</span>
              <span className={`pool-health ${p.health === 'ONLINE' ? 'ok' : 'bad'}`}>{p.health ?? '?'}</span>
              <span className="small muted num pool-size">
                {gib(p.used_gib)} / {gib(p.total_gib)}
              </span>
              <span className={`num tone-${tone(p.pct, 80, 90)}`}>{pct(p.pct)}</span>
            </div>
            <div className="bar-track">
              <div className={`bar-fill bar-${tone(p.pct, 80, 90)}`} style={{ width: `${p.pct ?? 0}%` }} />
            </div>
          </div>
        ))}
      </div>
    )
  }
  if (stats.disk_pct == null) return null
  return (
    <Gauge
      label="Disk"
      value={stats.disk_pct}
      detail={
        stats.disk_used_gb != null && stats.disk_total_gb != null
          ? `${Math.round(stats.disk_used_gb)} / ${Math.round(stats.disk_total_gb)} GB`
          : undefined
      }
    />
  )
}

function RichCard({
  machine,
  now,
  index,
  onOpen,
}: {
  machine: MachineStatus
  now: number
  index: number
  onOpen: () => void
}) {
  const Icon = ICONS[machine.icon] ?? Server
  const s = machine.stats
  const gpu = s?.gpus[0]
  const agentDown = s?.state && s.state !== 'up'
  return (
    <article
      className={`machine machine-rich card clickable status-${machine.status}`}
      style={{ ['--i' as string]: index }}
      onClick={onOpen}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onOpen())}
      role="button"
      tabIndex={0}
      aria-label={`${machine.name} details`}
    >
      <div className="machine-top">
        <div className="machine-icon">
          <Icon size={20} strokeWidth={1.6} />
        </div>
        <div className="machine-title">
          <h3>{machine.name}</h3>
          <p className="muted small">{machine.role}</p>
        </div>
        <StatusPill status={machine.status} />
      </div>

      {!s && (
        <p className="small muted waiting">
          {machine.status === 'down'
            ? machine.last_seen
              ? `Offline, last seen ${ago(machine.last_seen, now)}`
              : 'Offline'
            : 'Waiting for stats…'}
        </p>
      )}
      {s && agentDown && <p className="small warn-text">Stats agent is {s.state}.</p>}

      {s && (
        <>
          <div className="gauges">
            <Gauge
              label="CPU"
              value={s.cpu_pct}
              detail={s.load ? `load ${s.load.map((l) => l.toFixed(2)).join(' ')}` : undefined}
            />
            <Gauge
              label="Memory"
              value={s.mem_pct}
              detail={
                s.mem_used_gb != null && s.mem_total_gb != null
                  ? `${s.mem_used_gb.toFixed(1)} / ${s.mem_total_gb.toFixed(1)} GB${s.arc_gb ? ` · ARC ${s.arc_gb.toFixed(1)}` : ''}`
                  : undefined
              }
            />
            {gpu && (
              <Gauge
                label="GPU"
                value={gpu.util_pct}
                detail={
                  gpu.mem_total_mb
                    ? `${Math.round(gpu.mem_used_mb ?? 0)} / ${Math.round(gpu.mem_total_mb)} MB`
                    : undefined
                }
              />
            )}
          </div>

          {machine.spark && (
            <div className="spark-wrap" title="CPU and memory, last hour">
              <Sparkline
                series={[
                  { values: machine.spark.mem, className: 'spark-mem', label: 'memory' },
                  { values: machine.spark.cpu, className: 'spark-cpu', label: 'cpu' },
                ]}
              />
              <span className="spark-legend small muted">
                <i className="lg-cpu" /> CPU <i className="lg-mem" /> Mem · 1h
              </span>
            </div>
          )}

          <Storage stats={s} />

          <div className="stat-chips">
            {s.uptime_s != null && (
              <Chip icon={Clock} title="Uptime">
                {duration(s.uptime_s)}
              </Chip>
            )}
            {s.cpu_temp != null && (
              <Chip icon={Thermometer} title="CPU temperature">
                CPU {Math.round(s.cpu_temp)}°
              </Chip>
            )}
            {s.gpu_temp != null && (
              <Chip icon={Cpu} title="GPU temperature">
                GPU {Math.round(s.gpu_temp)}°
              </Chip>
            )}
            {s.drive_temp_max != null && (
              <Chip icon={HardDrive} title="Hottest drive">
                Drives {Math.round(s.drive_temp_max)}°
              </Chip>
            )}
            {s.net_tx_bps != null && (
              <Chip icon={ArrowUp} title="Upload">
                {rate(s.net_tx_bps)}
              </Chip>
            )}
            {s.net_rx_bps != null && (
              <Chip icon={ArrowDown} title="Download">
                {rate(s.net_rx_bps)}
              </Chip>
            )}
            {machine.latency_ms != null && (
              <Chip icon={Activity} title="Ping from the NAS">
                {latency(machine.latency_ms)}
              </Chip>
            )}
          </div>

        </>
      )}
      <AttachedActions target={machine.id} label={false} />
    </article>
  )
}

function CompactCard({ machine, now, onOpen }: { machine: MachineStatus; now: number; onOpen: () => void }) {
  const Icon = ICONS[machine.icon] ?? Server
  const state = machineState(machine)
  return (
    <button type="button" className={`machine-compact status-${state.status}`} onClick={onOpen}>
      <Icon size={16} strokeWidth={1.7} aria-hidden="true" />
      <div className="compact-text">
        <span className="compact-name">{machine.name}</span>
        <span className="small muted mono">{machine.address}</span>
      </div>
      <span className="small muted num compact-meta">
        {machine.status === 'up'
          ? latency(machine.latency_ms)
          : `${machine.away ? 'away · ' : ''}${machine.last_seen ? `seen ${ago(machine.last_seen, now)}` : 'not seen yet'}`}
      </span>
      <StatusDot status={state.status} label={state.label} />
    </button>
  )
}

/** Fallback for the local machine when no stats source is configured. */
function LocalCard({ machine, index, onOpen }: { machine: MachineStatus; index: number; onOpen: () => void }) {
  const Icon = ICONS[machine.icon] ?? Server
  const d = machine.details
  return (
    <article
      className={`machine machine-rich card clickable status-${machine.status}`}
      style={{ ['--i' as string]: index }}
      onClick={onOpen}
      role="button"
      tabIndex={0}
    >
      <div className="machine-top">
        <div className="machine-icon">
          <Icon size={20} strokeWidth={1.6} />
        </div>
        <div className="machine-title">
          <h3>{machine.name}</h3>
          <p className="muted small">{machine.role}</p>
        </div>
        <StatusPill status={machine.status} />
      </div>
      {d && (
        <div className="gauges">
          {d.load && d.cpus ? (
            <Gauge label={`Load (${d.cpus} threads)`} value={(d.load[0] / d.cpus) * 100} detail={d.load.join(' ')} />
          ) : null}
          {d.mem_used_pct != null && <Gauge label="Memory" value={d.mem_used_pct} detail={`of ${d.mem_total_gb} GB`} />}
        </div>
      )}
      {d?.uptime_s != null && <p className="small muted">Up {duration(d.uptime_s)}</p>}
    </article>
  )
}

export function Machines({ machines }: { machines: MachineStatus[] }) {
  const { open } = useApp()
  const now = useClock()
  const rich = machines.filter((m) => m.monitored || m.details)
  const compact = machines.filter((m) => !m.monitored && !m.details)

  return (
    <section className="section">
      <div className="section-head">
        <h2>Machines</h2>
        <span className="muted small">
          {machines.filter((m) => m.status === 'up').length} of {machines.filter((m) => !m.away).length} online
          {machines.some((m) => m.away) && ` · ${machines.filter((m) => m.away).length} away`}
        </span>
      </div>
      <div className="machines-rich">
        {rich.map((m, i) =>
          m.monitored ? (
            <RichCard key={m.id} machine={m} now={now} index={i} onOpen={() => open('machine', m.id)} />
          ) : (
            <LocalCard key={m.id} machine={m} index={i} onOpen={() => open('machine', m.id)} />
          ),
        )}
      </div>
      {compact.length > 0 && (
        <div className="machines-compact">
          {compact.map((m) => (
            <CompactCard key={m.id} machine={m} now={now} onOpen={() => open('machine', m.id)} />
          ))}
        </div>
      )}
    </section>
  )
}
