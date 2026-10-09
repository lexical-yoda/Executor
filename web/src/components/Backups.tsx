import { Archive, Database, FileArchive, Loader2 } from 'lucide-react'
import type { BackupRun, BackupStatus, Backups as BackupsData, DuplicatiJob, FileBackupStatus } from '../api'
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
      </div>
    </section>
  )
}
