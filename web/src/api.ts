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
  /** Found from Docker rather than listed in config.yaml. */
  discovered?: boolean
  /** The compose stack a discovered service stands for. */
  stack?: string | null
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
  /** Whether Executor keeps its own history (events, uptime, long-term stats, plays). */
  ledger: boolean
  edge: Edge | null
  backups: Backups | null
  media: Media | null
  jellyfin: JellyfinStatus | null
}

export interface Bandwidth {
  fetched_at: number | null
  age_s: number | null
  errors: Record<string, string>
  instance: {
    label: string | null
    plan: string | null
    region: string | null
  } | null
  month_start: number | null
  month_end: number | null
  elapsed_pct: number | null
  out_gb: number | null
  in_gb: number | null
  allowance_now_gb: number | null
  allowance_month_gb: number | null
  projected_out_gb: number | null
  projected_pct: number | null
  used_pct: number | null
  overage_gb: number | null
  overage_cost: number | null
  previous: {
    out_gb: number | null
    in_gb: number | null
    allowance_gb: number | null
  } | null
  daily: { date: string; out_gb: number; in_gb: number }[]
}

export interface Certificate {
  host: string
  expires: string | null
  days_left: number | null
  issuer: string | null
  error: string | null
}

export interface Edge {
  bandwidth: Bandwidth | null
  bandwidth_error: string | null
  certificates: Certificate[]
}

export type BackupStatus = 'ok' | 'warning' | 'failed' | 'stale' | 'running' | 'missing' | 'unknown'

export interface BackupRun {
  result: string
  finished: number | null
  warnings: number
  errors: number
  added_bytes: number | null
}

export interface DuplicatiJob {
  id: string
  name: string
  status: BackupStatus
  last_finished: number | null
  last_duration_s: number | null
  last_result: string | null
  last_error: string | null
  source_bytes: number | null
  target_bytes: number | null
  versions: number | null
  next_run: number | null
  repeat: string | null
  stale_after_s: number | null
  progress: { phase: string | null; fraction: number | null } | null
  history: BackupRun[]
}

export interface FileBackupStatus {
  name: string
  schedule: string
  status: BackupStatus
  last: number | null
  files: {
    name: string
    size: number | null
    mtime: number | null
    state: BackupStatus
  }[]
  kept: number | null
  log_line: string | null
  error: string | null
}

export interface StorageStatus {
  name: string
  bucket: string
  configured: boolean
  error: string | null
  aws: {
    fetched_at: number
    as_of: string | null
    bytes: number | null
    by_type: Record<string, number>
    objects: number | null
    growth_7d: number | null
    growth_30d: number | null
    growth_90d: number | null
    growth_total: number | null
    first_date: string | null
    monthly_cost: number | null
    series: { date: string; bytes: number }[]
  } | null
  duplicati_bytes: number | null
  duplicati_versions: number | null
  fallback_cost: number | null
}

export interface Backups {
  storage: StorageStatus[]
  checked_at: number | null
  duplicati: {
    configured: boolean
    ok: boolean
    error: string | null
    jobs: DuplicatiJob[]
    paused: boolean
  }
  files: FileBackupStatus[]
}

export interface MediaRequest {
  id: number
  kind: 'movie' | 'tv'
  tmdb_id: number
  title: string
  year: number | null
  has_poster: boolean
  seasons: number[]
  requested_by: string
  requested_at: string | null
  is_4k: boolean
}

export interface QueueItem {
  id: string
  source: 'sonarr' | 'radarr'
  title: string
  subtitle: string | null
  size: number
  progress: number | null
  eta_s: number | null
  status: string | null
  state: string | null
  health: string
  message: string | null
  client: string | null
}

export interface Media {
  requests: {
    configured: boolean
    ok: boolean
    error: string | null
    counts: Partial<Record<'total' | 'pending' | 'processing' | 'available' | 'declined', number>>
    pending: MediaRequest[]
    processing: MediaRequest[]
  }
  downloads: {
    sources: string[]
    queue: QueueItem[]
    errors: Record<string, string>
    torrents: {
      configured: boolean
      ok: boolean
      error: string | null
      down_bps?: number
      up_bps?: number
      down_session_bytes?: number
      up_session_bytes?: number
      connection?: string
      torrents?: number
      downloading?: number
      seeding?: number
      stalled?: number
      paused?: number
      errored?: number
    }
  }
}

export interface Place {
  city: string | null
  region: string | null
  country: string | null
  country_code: string | null
  lat: number
  lon: number
  /** How far off the database says it may be, in km (GeoLite2 only). */
  radius_km?: number | null
  /** geolite2, dbip, home (the server's own address) or correction (from the config). */
  source?: string | null
  /** Set when a vaguer database's area was narrowed to this city. */
  within?: { source: string; km: number } | null
}

export interface Watching {
  user: string
  user_id: string | null
  title: string
  kind: string | null
  client: string | null
  device: string | null
  ip: string | null
  paused: boolean
  transcoding: boolean
  progress: number | null
  runtime_s: number | null
  last_activity: string | null
  location: Place | null
  /** The other database's answer, when it names a different city. */
  location_alt: Place | null
}

export interface JellyfinStatus {
  ok: boolean
  error: string | null
  checked_at: number | null
  watching: Watching[]
  history: {
    enabled: boolean
    keep_days: number
    geo_ready: boolean
    geo_sources: { name: string; ready: boolean; version: string | null; error: string | null }[]
    corrections: number
    sightings: number
    error: string | null
  }
  hub: MapAnchor | null
  origin: MapAnchor | null
}

/** A fixed point on the map (home, the relay), optionally tied to a machine. */
export interface MapAnchor {
  label: string
  lat: number
  lon: number
  machine?: string | null
}

export interface MediaUser {
  id: string
  name: string
  sightings: number
  last_seen: number
  places: number
}

export interface PlaceGroup extends Place {
  corrected: boolean
  count: number
  first_seen: number
  last_seen: number
  users: { id: string; name: string; count: number; last_seen: number }[]
}

export interface Sighting extends Partial<Place> {
  id: number
  user_id: string
  user_name: string
  ip: string
  device: string | null
  client: string | null
  item: string | null
  source: 'session' | 'log'
  first_seen: number
  last_seen: number
}

export type HistoryRange = '1h' | '12h' | '24h' | '7d' | '30d' | '90d' | '1y'

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
  show_streams: boolean
  group: string | null
  attach: string[]
  steps: string[]
}

export interface Stream {
  user: string
  title: string
  client: string | null
  device: string | null
  paused: boolean
  transcoding: boolean
  last_activity: string | null
}

export interface Streams {
  configured: boolean
  ok: boolean
  error: string | null
  streams: Stream[]
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
  steps: {
    name: string
    status: StepStatus
    started_at: string | null
    finished_at: string | null
  }[]
}

export interface RunDetail extends RunSummary {
  lines: string[]
  next_offset: number
}

export type EventLevel = 'good' | 'bad' | 'warn' | 'info'

export interface LogEvent {
  id: number
  ts: number
  kind: string
  level: EventLevel
  title: string
  detail: string | null
  /** A person (shown blurred in presentation mode). */
  actor: string | null
  /** What the event is about, as "kind:id", for opening its details. */
  ref: string | null
}

export interface CheckBucket {
  t: number
  up: number
  degraded: number
  down: number
  ms: number | null
}

export interface ServiceHistory {
  service: string
  hours: number
  bucket_s: number
  buckets: CheckBucket[]
  uptime: number | null
}

export interface RecapTitle {
  title: string
  kind: string | null
  hours: number
  plays: number
  viewers: number
}

export interface Recap {
  days: number
  from: number
  to: number
  media: {
    plays: number
    hours: number
    viewers: number
    titles: number
    countries: number
    top_titles: RecapTitle[]
    top_viewers: { id: string; name: string; hours: number; plays: number }[]
    top_places: { city: string; country_code: string | null; plays: number; viewers: number }[]
    prime_hour: number | null
    longest: { user_id: string; name: string; title: string; episode: string | null; hours: number } | null
  }
  previous: { plays: number; hours: number; viewers: number; titles: number }
  downloads: { bytes: number; bytes_before: number; completed: number; failed: number; requests: number }
  reliability: {
    uptime: number | null
    worst: { service: string; uptime: number } | null
    incidents: number
    machine_outages: number
    restarts: number
  }
  backups: { succeeded: number; failed: number; warnings: number; glacier_growth: number | null }
  actions: { succeeded: number; failed: number }
  machines: Record<string, Record<string, number | null>>
}

export interface Tileset {
  name: string
  url: string
  bytes: number
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: 'application/json', ...init?.headers },
  })
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
  streams: () => request<Streams>('/api/streams'),
  mediaUsers: (days: number) => request<{ users: MediaUser[] }>(`/api/media/users?days=${days}`),
  mediaPlaces: (days: number, user?: string) =>
    request<{ places: PlaceGroup[]; unlocated: number }>(
      `/api/media/places?days=${days}${user ? `&user=${encodeURIComponent(user)}` : ''}`,
    ),
  mediaTrail: (user: string, days: number) =>
    request<{ user: string; sightings: Sighting[] }>(`/api/media/trail?user=${encodeURIComponent(user)}&days=${days}`),
  runs: () => request<{ busy: string | null; runs: RunSummary[] }>('/api/runs'),
  events: (before?: number, limit = 50) =>
    request<{ events: LogEvent[] }>(`/api/events?limit=${limit}${before ? `&before=${before}` : ''}`),
  serviceHistory: (id: string, hours: number) =>
    request<ServiceHistory>(`/api/services/${encodeURIComponent(id)}/history?hours=${hours}`),
  recap: (days = 7) => request<Recap>(`/api/recap?days=${days}`),
  tilesets: () => request<{ tilesets: Tileset[] }>('/api/map/tilesets'),
  run: (id: string, offset: number) => request<RunDetail>(`/api/runs/${id}?offset=${offset}`),
  start: (id: string) =>
    request<RunDetail>(`/api/actions/${encodeURIComponent(id)}/run`, {
      method: 'POST',
      // The custom header is required by the server's cross-site request guard.
      headers: { 'Content-Type': 'application/json', 'X-Executor': '1' },
      body: '{}',
    }),
}
