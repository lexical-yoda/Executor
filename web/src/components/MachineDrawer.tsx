import { Loader2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, type HistoryRange, type MachineHistory } from '../api'
import { ago, duration, gib, latency, machineState, pct, rate } from '../format'
import { useApp, useClock } from '../state'
import { AttachedActions } from './ActionKit'
import { Chart } from './Chart'
import { StatusPill } from './StatusDot'
import { Empty, Facts, SourceNote } from './ui'

const RANGES: { id: HistoryRange; label: string }[] = [
  { id: '1h', label: '1h' },
  { id: '24h', label: '24h' },
  { id: '7d', label: '7d' },
  { id: '30d', label: '30d' },
  { id: '90d', label: '90d' },
  { id: '1y', label: '1y' },
]

export function MachineDrawer({ id }: { id: string }) {
  const { snapshot } = useApp()
  const now = useClock()
  const machine = snapshot?.machines.find((m) => m.id === id)
  const [range, setRange] = useState<HistoryRange>('24h')
  const [data, setData] = useState<MachineHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const charted = !!machine && (machine.monitored || snapshot?.ledger)

  useEffect(() => {
    if (!charted) return
    let live = true
    setLoading(true)
    setError(null)
    api
      .history(id, range)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [id, range, charted])

  const charts = useMemo(() => {
    if (!data) return null
    const percent = (v: number) => `${Math.round(v)}%`
    const pools = Object.entries(data.pools)
    return (
      <div className="charts charts-drawer">
        <Chart title="CPU" t={data.t} lines={[{ label: 'CPU', values: data.cpu }]} format={percent} max={100} />
        <Chart title="Memory" t={data.t} lines={[{ label: 'Memory', values: data.mem }]} format={percent} max={100} />
        <Chart
          title="Network"
          t={data.t}
          lines={[
            { label: 'Down', values: data.net_rx },
            { label: 'Up', values: data.net_tx },
          ]}
          format={(v) => rate(v)}
        />
        {pools.length ? (
          <Chart
            title="Storage pools"
            t={data.t}
            lines={pools.map(([name, values]) => ({ label: name, values }))}
            format={percent}
            max={100}
          />
        ) : (
          <Chart title="Disk" t={data.t} lines={[{ label: 'Disk', values: data.disk }]} format={percent} max={100} />
        )}
        <Chart
          title="Temperatures"
          t={data.t}
          lines={[
            { label: 'CPU', values: data.cpu_temp },
            { label: 'GPU', values: data.gpu_temp },
          ]}
          format={(v) => `${Math.round(v)}°`}
        />
        <Chart title="GPU" t={data.t} lines={[{ label: 'GPU', values: data.gpu }]} format={percent} max={100} />
      </div>
    )
  }, [data])

  if (!machine) return <Empty>This machine is not in the configuration.</Empty>
  const s = machine.stats
  const longRange = range === '90d' || range === '1y'
  return (
    <div>
      <div className="drawer-kicker">
        Machine <StatusPill {...machineState(machine)} />
      </div>
      <h3 className="drawer-title">{machine.name}</h3>
      <p className="muted">{machine.role}</p>
      <Facts
        items={[
          ['Address', machine.address ? <span className="mono">{machine.address}</span> : null],
          ['Ping', machine.latency_ms != null ? latency(machine.latency_ms) : null],
          ['Last seen', machine.last_seen ? ago(machine.last_seen, now) : null],
          ['Uptime', s?.uptime_s != null ? duration(s.uptime_s) : null],
          ['CPU', s ? `${pct(s.cpu_pct)}${s.threads ? ` of ${s.threads} threads` : ''}` : null],
          [
            'Memory',
            s && s.mem_used_gb != null && s.mem_total_gb != null
              ? `${pct(s.mem_pct)} · ${s.mem_used_gb.toFixed(1)} / ${s.mem_total_gb.toFixed(1)} GB`
              : null,
          ],
          ['Load', s?.load ? s.load.map((l) => l.toFixed(2)).join(' ') : null],
          ['CPU temp', s?.cpu_temp != null ? `${Math.round(s.cpu_temp)}°C` : null],
          ['GPU', s?.gpus[0] ? `${s.gpus[0].name ?? 'GPU'} · ${pct(s.gpus[0].util_pct)}` : null],
          ['Hottest drive', s?.drive_temp_max != null ? `${Math.round(s.drive_temp_max)}°C` : null],
          ['Network', s && s.net_rx_bps != null ? `↓ ${rate(s.net_rx_bps)} · ↑ ${rate(s.net_tx_bps)}` : null],
          [
            'Pools',
            s?.pools.length
              ? s.pools.map((p) => `${p.name} ${gib(p.used_gib)} / ${gib(p.total_gib)} (${p.health ?? '?'})`).join(' · ')
              : null,
          ],
          ['Error', machine.status === 'down' && !machine.away ? machine.error : null],
        ]}
      />
      <AttachedActions target={machine.id} />
      {charted && (
        <>
          <div className="drawer-row">
            <h4 className="drawer-sub">History</h4>
            <div className="range-tabs" role="tablist">
              {RANGES.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  role="tab"
                  aria-selected={range === r.id}
                  className={range === r.id ? 'active' : ''}
                  onClick={() => setRange(r.id)}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>
          {loading && !data && (
            <p className="muted small">
              <Loader2 size={13} className="spin" /> Loading history…
            </p>
          )}
          {error && <p className="error small">Could not load history: {error}</p>}
          {charts}
          {longRange && data && (
            <p className="small muted">
              Hourly averages kept by Executor{data.t.length ? `, from ${new Date(data.t[0] * 1000).toLocaleDateString()}` : ''}. The
              stats source keeps about a month; Executor keeps a year.
            </p>
          )}
        </>
      )}
      <SourceNote source={machine.monitored ? 'Beszel and ping from the NAS' : 'ping from the NAS'} at={s?.updated ?? machine.last_seen} />
    </div>
  )
}
