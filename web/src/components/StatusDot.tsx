import type { Status } from '../api'
import { statusLabel } from '../format'
import { Badge, STATUS_TONE } from './Badge'

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
    <Badge tone={STATUS_TONE[status]} dot>
      {label ?? statusLabel[status]}
    </Badge>
  )
}
