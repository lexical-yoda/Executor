/** Tiny inline chart for 0-100 values. Gaps (null) break the line. */
export function Sparkline({
  series,
  height = 34,
}: {
  series: { values: (number | null)[]; className: string; label: string }[]
  height?: number
}) {
  const width = 120
  const longest = Math.max(2, ...series.map((s) => s.values.length))
  const x = (i: number) => (i / (longest - 1)) * width
  const y = (v: number) => height - 2 - (Math.min(100, Math.max(0, v)) / 100) * (height - 4)

  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      {series.map((s) => {
        const parts: string[] = []
        let pen = false
        s.values.forEach((v, i) => {
          if (v === null) {
            pen = false
            return
          }
          parts.push(`${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
          pen = true
        })
        return <path key={s.label} d={parts.join(' ')} className={s.className} />
      })}
    </svg>
  )
}
