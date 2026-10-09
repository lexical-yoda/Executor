import { Archive, CloudUpload, Database, FileArchive, Loader2 } from 'lucide-react'
import type {
  BackupRun,
  BackupStatus,
  Backups as BackupsData,
  DuplicatiJob,
  FileBackupStatus,
  StorageStatus,
} from '../api'
import { ago, bytes, duration, until } from '../format'

const LABEL: Record<BackupStatus, string> = {
  ok: 'OK',
  warning: 'Warnings',
  failed: 'Failed',
  stale: 'Overdue',
  running: 'Running',
  missing: 'Missing',
  unknown: 'Unknown',
}

function StatusPill({ status }: { status: BackupStatus }) {
  return (
    <span className={`backup-pill bk-${status}`}>
      {status === 'running' && <Loader2 size={11} className="spin" />}
      {LABEL[status]}
    </span>
  )
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function runTone(result: string): string {
  if (result === 'Success') return 'ok'
  if (result === 'Warning') return 'warning'
  if (result === 'Error' || result === 'Fatal') return 'failed'
  return 'unknown'
}

function RunStrip({ runs }: { runs: BackupRun[] }) {
  if (!runs.length) return null
  return (
    <div className="run-strip" aria-label="Recent runs, oldest first">
      {runs.map((r, i) => (
        <span
          key={i}
          className={`run-cell bk-${runTone(r.result)}${i === runs.length - 1 ? ' latest' : ''}`}
          title={`${r.result}${r.finished ? ` · ${when(r.finished)}` : ''}${r.warnings ? ` · ${r.warnings} warnings` : ''}${
            r.errors ? ` · ${r.errors} errors` : ''
          }${r.added_bytes !== null ? ` · +${bytes(r.added_bytes)}` : ''}`}
        />
      ))}
      <span className="small muted run-strip-label">last {runs.length}</span>
    </div>
  )
}

function JobCard({ job, now, index }: { job: DuplicatiJob; now: number; index: number }) {
  const latest = job.history[job.history.length - 1]
  const fraction = job.progress?.fraction
  return (
    <article className={`backup-card card bk-edge-${job.status}`} style={{ ['--i' as string]: index }}>
      <div className="edge-head">
        <Archive size={16} />
        <h3>{job.name}</h3>
        <StatusPill status={job.status} />
      </div>

      {job.status === 'running' ? (
        <div className="backup-progress">
          <div className="bw-bar">
            <div className="bw-fill bar-data" style={{ width: `${Math.round((fraction ?? 0) * 100)}%` }} />
          </div>
          <span className="small muted">
            {job.progress?.phase?.replace(/_/g, ' ') ?? 'Working'}
            {fraction != null ? ` · ${Math.round(fraction * 100)}%` : ''}
          </span>
        </div>
      ) : (
        <div className="backup-main">
          <span className="backup-big num">{job.last_finished ? ago(job.last_finished * 1000, now) : 'never'}</span>
          <span className="small muted">
            {job.last_duration_s !== null && `took ${duration(job.last_duration_s)}`}
            {latest?.added_bytes != null && ` · +${bytes(latest.added_bytes)}`}
          </span>
        </div>
      )}

      <RunStrip runs={job.history} />

      <div className="stat-chips">
        <span className="stat-chip">
          <span className="num">{bytes(job.source_bytes)}</span> source
        </span>
        <span className="stat-chip">
          <span className="num">{bytes(job.target_bytes)}</span> stored
        </span>
        {job.versions !== null && (
          <span className="stat-chip">
            <span className="num">{job.versions}</span> versions
          </span>
        )}
        {job.next_run && (
          <span className="stat-chip" title={when(job.next_run)}>
            Next {until(job.next_run * 1000, now)}
          </span>
        )}
      </div>
      {job.last_error && <p className="small warn-text">{job.last_error}</p>}
    </article>
  )
}

function FileCard({ item, now, index }: { item: FileBackupStatus; now: number; index: number }) {
  const single = item.kept !== null
  return (
    <article className={`backup-card card bk-edge-${item.status}`} style={{ ['--i' as string]: index }}>
      <div className="edge-head">
        {single ? <FileArchive size={16} /> : <Database size={16} />}
        <h3>{item.name}</h3>
        <StatusPill status={item.status} />
      </div>
      <div className="backup-main">
        <span className="backup-big num">{item.last ? ago(item.last * 1000, now) : 'never'}</span>
        <span className="small muted">{item.schedule}</span>
      </div>
      {!single && (
        <ul className="backup-files">
          {item.files.map((f) => (
            <li key={f.name}>
              <span className={`file-dot bk-${f.state}`} title={LABEL[f.state]} />
              <span className="mono">{f.name}</span>
              <span className="muted num">{f.size !== null ? bytes(f.size) : LABEL[f.state].toLowerCase()}</span>
            </li>
          ))}
        </ul>
      )}
      {single && (
        <div className="stat-chips">
          {item.files[0]?.size != null && (
            <span className="stat-chip">
              Latest <span className="num">{bytes(item.files[0].size)}</span>
            </span>
          )}
          <span className="stat-chip">
            <span className="num">{item.kept}</span> kept
          </span>
        </div>
      )}
      {item.log_line && <p className="backup-log mono small">{item.log_line}</p>}
      {item.error && <p className="small warn-text">{item.error}</p>}
    </article>
  )
}

const STORAGE_LABEL: Record<string, string> = {
  GlacierInstantRetrievalStorage: 'Glacier Instant Retrieval',
  GlacierInstantRetrievalSizeOverhead: 'small-object overhead',
  StandardStorage: 'Standard',
}

function signed(value: number | null): string {
  if (value === null) return '—'
  return `${value >= 0 ? '+' : '−'}${bytes(Math.abs(value))}`
}

function day(date: string): string {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' })
}

function SizeChart({ series }: { series: { date: string; bytes: number }[] }) {
  if (series.length < 2) return null
  const values = series.map((p) => p.bytes)
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = Math.max(1, max - min)
  // Leave headroom so a flat line sits mid-chart rather than on an edge.
  const y = (v: number) => 34 - ((v - min) / span) * 28
  const x = (i: number) => (i / (series.length - 1)) * 100
  const line = series.map((p, i) => `${x(i).toFixed(2)},${y(p.bytes).toFixed(2)}`).join(' ')
  return (
    <div className="size-chart">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="size-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--data)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--data)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={`0,40 ${line} 100,40`} fill="url(#size-fill)" />
        <polyline points={line} fill="none" stroke="var(--data)" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="daily-legend small muted num">
        <span>{day(series[0].date)}</span>
        <span>
          {bytes(min)} – {bytes(max)}
        </span>
        <span>{day(series[series.length - 1].date)}</span>
      </div>
    </div>
  )
}

function StorageCard({ item, now, index }: { item: StorageStatus; now: number; index: number }) {
  const aws = item.aws
  const fromAws = aws?.bytes != null
  const status: BackupStatus = fromAws ? 'ok' : item.error ? 'warning' : 'unknown'
  return (
    <article className={`backup-card storage-card card bk-edge-${status}`} style={{ ['--i' as string]: index }}>
      <div className="edge-head">
        <CloudUpload size={16} />
        <h3>{item.name}</h3>
        <span className="small muted mono">{item.bucket}</span>
      </div>

      <div className="backup-main">
        <span className="backup-big num">{bytes(fromAws ? aws!.bytes : item.duplicati_bytes)}</span>
        <span className="small muted">
          {fromAws
            ? `stored in AWS, as of ${day(aws!.as_of!)}`
            : item.duplicati_bytes !== null
              ? 'as reported by Duplicati (AWS figures unavailable)'
              : 'no figures yet'}
        </span>
      </div>

      {fromAws && <SizeChart series={aws!.series} />}

      <div className="stat-chips">
        {fromAws && (
          <>
            <span className="stat-chip">
              <span className="num">{signed(aws!.growth_30d)}</span> in 30 days
            </span>
            <span className="stat-chip">
              <span className="num">{signed(aws!.growth_90d)}</span> in 90 days
            </span>
            {aws!.objects !== null && (
              <span className="stat-chip">
                <span className="num">{aws!.objects.toLocaleString()}</span> objects
              </span>
            )}
          </>
        )}
        {(fromAws ? aws!.monthly_cost : item.fallback_cost) !== null && (
          <span className="stat-chip" title="Storage only, at list price; requests and early deletion are extra">
            ≈ <span className="num">${(fromAws ? aws!.monthly_cost! : item.fallback_cost!).toFixed(2)}</span>/month
          </span>
        )}
        {fromAws && item.duplicati_bytes !== null && (
          <span className="stat-chip" title="Duplicati's own count of what it stored">
            Duplicati: <span className="num">{bytes(item.duplicati_bytes)}</span>
            {item.duplicati_versions !== null && ` · ${item.duplicati_versions} versions`}
          </span>
        )}
      </div>

      {fromAws && Object.keys(aws!.by_type).length > 1 && (
        <p className="small muted">
          {Object.entries(aws!.by_type)
            .map(([type, size]) => `${STORAGE_LABEL[type] ?? type} ${bytes(size)}`)
            .join(' · ')}
        </p>
      )}
      {item.error && <p className="small warn-text">AWS: {item.error}</p>}
      {!item.configured && <p className="small muted">AWS key not set; showing Duplicati's figure.</p>}
      {fromAws && <span className="small muted">Checked {ago(aws!.fetched_at * 1000, now)} · AWS updates daily</span>}
    </article>
  )
}

export function Backups({ backups, now }: { backups: BackupsData; now: number }) {
  const { duplicati, files } = backups
  return (
    <section className="section">
      <div className="section-head">
        <h2>Backups</h2>
        {duplicati.paused && <span className="small warn-text">Duplicati is paused</span>}
      </div>
      {duplicati.configured && !duplicati.ok && (
        <p className="small warn-text">Duplicati unavailable: {duplicati.error}</p>
      )}
      <div className="backup-grid">
        {duplicati.jobs.map((job, i) => (
          <JobCard key={job.id} job={job} now={now} index={i} />
        ))}
        {files.map((item, i) => (
          <FileCard key={item.name} item={item} now={now} index={duplicati.jobs.length + i} />
        ))}
        {backups.storage.map((item, i) => (
          <StorageCard key={item.name} item={item} now={now} index={duplicati.jobs.length + files.length + i} />
        ))}
      </div>
    </section>
  )
}
