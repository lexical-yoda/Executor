import { LineChart, Loader2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { api, type HistoryRange, type MachineHistory, type MachineStatus } from '../api'
import { rate } from '../format'
import { Modal } from './Modal'

const RANGES: { id: HistoryRange; label: string }[] = [
  { id: '1h', label: '1 hour' },
  { id: '12h', label: '12 hours' },
  { id: '24h', label: '24 hours' },
  { id: '7d', label: '7 days' },
  { id: '30d', label: '30 days' },
]

const PALETTE = ['#38bdf8', '#f5a524', '#34d399', '#f472b6', '#a78bfa', '#fb7185']

type Line = { label: string; values: (number | null)[] | null }

function Chart({
  title,
  t,
  lines,
  format,
  max,
}: {
  title: string
  t: number[]
  lines: Line[]
  format: (v: number) => string
  max?: number
}) {
  const box = useRef<HTMLDivElement>(null)
  const present = lines.filter((l): l is { label: string; values: (number | null)[] } => !!l.values)

  useEffect(() => {
    const el = box.current
    if (!el || !present.length || t.length < 2) return
    const axis = { stroke: '#8a97ab', grid: { stroke: 'rgba(148,163,184,0.10)' }, ticks: { stroke: 'rgba(148,163,184,0.15)' } }
    const plot = new uPlot(
      {
        width: el.clientWidth,
        height: 170,
        legend: { show: true, live: true },
        cursor: { points: { size: 6 } },
        scales: { y: max !== undefined ? { range: [0, max] } : { auto: true, range: (_u, _min, hi) => [0, hi * 1.1 || 1] } },
        axes: [axis, { ...axis, size: 72, values: (_u, ticks) => ticks.map(format) }],
        series: [
          {},
          ...present.map((l, i) => ({
            label: l.label,
            stroke: PALETTE[i % PALETTE.length],
            width: 1.6,
            fill: `${PALETTE[i % PALETTE.length]}18`,
            value: (_u: uPlot, v: number | null) => (v == null ? '—' : format(v)),
          })),
        ],
      },
      [t, ...present.map((l) => l.values)] as uPlot.AlignedData,
      el,
    )
    const resize = new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height: 170 }))
    resize.observe(el)
    return () => {
      resize.disconnect()
      plot.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, lines])

  if (!present.length) return null
  return (
    <div className="chart card-flat">
      <h4>{title}</h4>
      {t.length < 2 ? <p className="small muted">Not enough history yet.</p> : <div ref={box} className="chart-box" />}
    </div>
  )
}

export default function MachineDetail({ machine, onClose }: { machine: MachineStatus; onClose: () => void }) {
  const [range, setRange] = useState<HistoryRange>('24h')
  const [data, setData] = useState<MachineHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    api
      .history(machine.id, range)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [machine.id, range])

  const charts = useMemo(() => {
    if (!data) return null
    const percent = (v: number) => `${Math.round(v)}%`
    const pools = Object.entries(data.pools)
    return (
      <div className="charts">
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

  return (
    <Modal onClose={onClose} label={`${machine.name} history`} wide>
      <div className="modal-head">
        <LineChart size={18} />
        <h3>{machine.name}</h3>
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
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      {loading && !data && (
        <p className="muted small">
          <Loader2 size={13} className="spin" /> Loading history…
        </p>
      )}
      {error && <p className="error">Could not load history: {error}</p>}
      {charts}
    </Modal>
  )
}
