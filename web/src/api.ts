export type Status = 'up' | 'down' | 'degraded' | 'unknown'

export interface ContainerState {
  name: string
  state: string
  health: string | null
  status: string
}

export interface ServiceStatus {
  id: string
  name: string
  group: string
  url: string | null
  description: string | null
  status: Status
  latency_ms: number | null
  http_status: number | null
  error: string | null
  checked_at: string | null
  containers: ContainerState[]
}

export interface MachineDetails {
  load?: number[]
  cpus?: number
  mem_total_gb?: number | null
  mem_used_pct?: number | null
  uptime_s?: number
}

export type MachineIcon = 'server' | 'cloud' | 'laptop' | 'phone' | 'desktop' | 'gamepad' | 'router'

export interface PoolStats {
  name: string
  used_gib: number
  total_gib: number
  pct: number | null
  health: string | null
}

export interface GpuStats {
  name: string | null
  util_pct: number | null
  mem_used_mb: number | null
  mem_total_mb: number | null
  power_w: number | null
}

export interface MachineStats {
  state: 'up' | 'down' | 'paused' | 'pending' | null
  threads: number | null
  cpu_pct: number | null
  mem_pct: number | null
  mem_used_gb: number | null
  mem_total_gb: number | null
  arc_gb: number | null
  disk_pct: number | null
  disk_used_gb: number | null
  disk_total_gb: number | null
  load: number[] | null
  uptime_s: number | null
  cpu_temp: number | null
  gpu_temp: number | null
  drive_temp_max: number | null
  net_tx_bps: number | null
  net_rx_bps: number | null
  gpus: GpuStats[]
  pools: PoolStats[]
  updated: string | null
}

export interface MachineStatus {
  id: string
  name: string
  role: string
  address: string | null
  icon: MachineIcon
  monitored: boolean
  stats: MachineStats | null
  spark: { cpu: (number | null)[]; mem: (number | null)[] } | null
  status: Status
  latency_ms: number | null
  error: string | null
  last_seen: string | null
  details: MachineDetails | null
}

export interface Snapshot {
  generated_at: string
  site: { title: string; subtitle: string }
  groups: string[]
  machines: MachineStatus[]
  services: ServiceStatus[]
  summary: {
    services_up: number
    services_total: number
    machines_up: number
    machines_total: number
    containers_running: number
    containers_total: number
    containers_unhealthy: number
    containers_known: boolean
  }
  runner: { ok: boolean; error: string | null }
  beszel: { configured: boolean; ok: boolean; error: string | null }
}

export type HistoryRange = '1h' | '12h' | '24h' | '7d' | '30d'

export interface MachineHistory {
  machine: string
  range: HistoryRange
  t: number[]
  cpu: (number | null)[]
  mem: (number | null)[]
  disk: (number | null)[]
  net_tx: (number | null)[] | null
  net_rx: (number | null)[] | null
  cpu_temp: (number | null)[] | null
  gpu: (number | null)[] | null
  gpu_temp: (number | null)[] | null
  pools: Record<string, (number | null)[]>
}

export interface ActionInfo {
  id: string
  title: string
  description: string
  confirm: string
  danger: 'low' | 'medium' | 'high'
  steps: string[]
}

export type StepStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'

export interface RunSummary {
  id: string
  action: string
  title: string
  requested_by: string
  started_at: string
  finished_at: string | null
  status: 'running' | 'succeeded' | 'failed'
  error: string | null
  steps: { name: string; status: StepStatus; started_at: string | null; finished_at: string | null }[]
}

export interface RunDetail extends RunSummary {
  lines: string[]
  next_offset: number
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { Accept: 'application/json', ...init?.headers } })
  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!response.ok) {
    const detail = (body as { detail?: string } | null)?.detail ?? (text || response.statusText)
    throw new ApiError(response.status, detail)
  }
  return body as T
}

export const api = {
  status: () => request<Snapshot>('/api/status'),
  history: (machine: string, range: HistoryRange) =>
    request<MachineHistory>(`/api/machines/${encodeURIComponent(machine)}/history?range=${range}`),
  actions: () => request<ActionInfo[]>('/api/actions'),
  runs: () => request<{ busy: string | null; runs: RunSummary[] }>('/api/runs'),
  run: (id: string, offset: number) => request<RunDetail>(`/api/runs/${id}?offset=${offset}`),
  start: (id: string) =>
    request<RunDetail>(`/api/actions/${encodeURIComponent(id)}/run`, {
      method: 'POST',
      // The custom header is required by the server's cross-site request guard.
      headers: { 'Content-Type': 'application/json', 'X-Executor': '1' },
      body: '{}',
    }),
}
