import type { Status } from './api'

export function ago(iso: string | null | number, now: number): string {
  if (iso === null) return 'never'
  const then = typeof iso === 'number' ? iso : Date.parse(iso)
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function duration(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

export function latency(ms: number | null): string {
  if (ms === null) return '—'
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`
}

export const statusLabel: Record<string, string> = {
  up: 'Online',
  down: 'Down',
  degraded: 'Degraded',
  unknown: 'Unknown',
}

/** How a machine shows: a roaming device (laptop, phone) that is offline is away, not down. */
export function machineState(m: { status: Status; away?: boolean }): { status: Status; label: string } {
  return m.away ? { status: 'unknown', label: 'Away' } : { status: m.status, label: statusLabel[m.status] }
}

export function rate(bytesPerSecond: number | null): string {
  if (bytesPerSecond === null) return '—'
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s']
  let value = bytesPerSecond
  let i = 0
  while (value >= 1000 && i < units.length - 1) {
    value /= 1000
    i += 1
  }
  return `${value < 10 && i > 0 ? value.toFixed(1) : Math.round(value)} ${units[i]}`
}

export function gib(value: number): string {
  return value >= 1024 ? `${(value / 1024).toFixed(1)} TiB` : `${Math.round(value)} GiB`
}

export function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${value < 10 ? value.toFixed(1) : Math.round(value)}%`
}

export function bytes(value: number | null): string {
  if (value === null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let n = value
  let i = 0
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i += 1
  }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`
}

export function until(ms: number, now: number): string {
  const seconds = Math.round((ms - now) / 1000)
  if (seconds <= 60) return 'due now'
  return `in ${duration(seconds)}`
}

/** Who asked for a run: the machine's name when its address is a known one. */
export function requester(address: string, machines: { name: string; address: string | null }[] | undefined): string {
  return machines?.find((m) => m.address === address)?.name ?? address
}
