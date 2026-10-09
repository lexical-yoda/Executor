import type { Status } from '../api'
import { statusLabel } from '../format'

export function StatusDot({ status, size = 10 }: { status: Status; size?: number }) {
  return (
    <span
      className={`dot dot-${status}`}
      style={{ width: size, height: size }}
      role="img"
      aria-label={statusLabel[status]}
    />
  )
}

export function StatusPill({ status }: { status: Status }) {
  return (
    <span className={`pill pill-${status}`}>
      <StatusDot status={status} size={7} />
      {statusLabel[status]}
    </span>
  )
}
