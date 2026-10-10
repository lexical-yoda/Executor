import type { Status } from '../api'
import { statusLabel } from '../format'

export function StatusDot({ status, size = 10, label }: { status: Status; size?: number; label?: string }) {
  return (
    <span
      className={`dot dot-${status}`}
      style={{ width: size, height: size }}
      role="img"
      aria-label={label ?? statusLabel[status]}
    />
  )
}

export function StatusPill({ status, label }: { status: Status; label?: string }) {
  return (
    <span className={`pill pill-${status}`}>
      <StatusDot status={status} size={7} label={label} />
      {label ?? statusLabel[status]}
    </span>
  )
}
