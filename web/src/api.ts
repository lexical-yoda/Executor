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

export interface MachineStatus {
  id: string
  name: string
  role: string
  address: string | null
  icon: MachineIcon
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
