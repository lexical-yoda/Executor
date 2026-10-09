import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

const PALETTE = ['#38bdf8', '#f5a524', '#34d399', '#f472b6', '#a78bfa', '#fb7185']

export type Line = { label: string; values: (number | null)[] | null }

/** A time series chart with a live legend. */
export function Chart({
  title,
  t,
  lines,
  format,
  max,
  height = 160,
}: {
  title: string
  t: number[]
  lines: Line[]
  format: (v: number) => string
  max?: number
  height?: number
}) {
  const box = useRef<HTMLDivElement>(null)
  const present = lines.filter((l): l is { label: string; values: (number | null)[] } => !!l.values)

  useEffect(() => {
    const el = box.current
    if (!el || !present.length || t.length < 2) return
    const axis = {
      stroke: '#8a97ab',
      grid: { stroke: 'rgba(148,163,184,0.10)' },
      ticks: { stroke: 'rgba(148,163,184,0.15)' },
    }
    const plot = new uPlot(
      {
        width: el.clientWidth,
        height,
        legend: { show: true, live: true },
        cursor: { points: { size: 6 } },
        scales: {
          y: max !== undefined ? { range: [0, max] } : { auto: true, range: (_u, _min, hi) => [0, hi * 1.1 || 1] },
        },
        axes: [axis, { ...axis, size: 64, values: (_u, ticks) => ticks.map(format) }],
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
    const resize = new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height }))
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
