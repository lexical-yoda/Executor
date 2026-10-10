/** The one segmented picker for a time range (or a view), used by every
 *  drawer and the map: the same look, labels such as "24h", "7d", "30d". */
export function RangePicker<T extends string | number>({
  options,
  value,
  onChange,
  label,
}: {
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
  /** What the picker chooses, for screen readers ("Days", "Map view"). */
  label?: string
}) {
  return (
    <div className="range-tabs" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          className={value === o.value ? 'active' : ''}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Options from plain day counts: 7 -> "7d". */
export const days = (...counts: number[]) => counts.map((d) => ({ value: d, label: `${d}d` }))
