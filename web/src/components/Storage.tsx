import { AlertTriangle, Database, HardDrive, Info, Thermometer } from 'lucide-react'
import type { MouseEvent } from 'react'
import type { Disk, NasAlert, Pool, TrueNASHealth } from '../api'
import { ago, bytes, duration } from '../format'
import { useApp, useClock } from '../state'
import { DeckSection, Empty, Facts, RowButton, SourceNote } from './ui'
import { Badge, sentence, STATUS_TONE } from './Badge'

/** How worrying a disk temperature is: hard drives run cooler than SSDs. */
export function tempTone(disk: Pick<Disk, 'type'>, temp: number | null | undefined): 'up' | 'degraded' | 'down' | 'unknown' {
  if (temp == null) return 'unknown'
  const [warm, hot] = disk.type === 'HDD' ? [50, 55] : [70, 80]
  return temp >= hot ? 'down' : temp >= warm ? 'degraded' : 'up'
}

export function poolTone(pool: Pool): 'up' | 'degraded' | 'down' {
  if (pool.status !== 'ONLINE' || !pool.healthy) return pool.status === 'DEGRADED' ? 'degraded' : 'down'
  if (pool.scan?.errors || pool.vdevs.some((v) => v.errors)) return 'degraded'
  return pool.warning ? 'degraded' : 'up'
}

const BK: Record<string, string> = { up: 'ok', degraded: 'warning', down: 'failed', unknown: 'unknown' }

function scanLine(pool: Pool, now: number): string {
  const scan = pool.scan
  if (!scan) return 'Never scrubbed'
  const word = (scan.function ?? 'scrub').toLowerCase()
  if (scan.state === 'SCANNING') return `${word[0].toUpperCase()}${word.slice(1)} running${scan.pct != null ? ` · ${scan.pct}%` : ''}`
  const errors = scan.errors ? `${scan.errors} errors` : 'no errors'
  return scan.finished ? `Last ${word} ${ago(scan.finished * 1000, now)} · ${errors}` : `Last ${word}: ${scan.state?.toLowerCase()}`
}

function TempChip({ disk }: { disk: Disk }) {
  const { open } = useApp()
  return (
    <button
      type="button"
      className={`temp-chip tone-${tempTone(disk, disk.temp)}`}
      onClick={(e) => {
        e.stopPropagation()
        open('disk', disk.name)
      }}
      title={`${disk.name} · ${disk.model ?? ''}`}
    >
      <span className="mono">{disk.name}</span>
      <span className="num">{disk.temp != null ? `${Math.round(disk.temp)}°` : '—'}</span>
    </button>
  )
}

function PoolCard({ pool, disks, index }: { pool: Pool; disks: Disk[]; index: number }) {
  const { open } = useApp()
  const now = useClock()
  const tone = poolTone(pool)
  const layout = pool.vdevs
    .filter((v) => v.role === 'data')
    .map((v) => `${v.type ?? 'disk'} · ${v.disks.length} ${v.disks.length === 1 ? 'disk' : 'disks'}`)
  return (
    <article
      className={`backup-card card clickable pool-card bk-edge-${BK[tone]}`}
      style={{ ['--i' as string]: index }}
      onClick={() => open('pool', pool.name)}
      role="button"
      tabIndex={0}
    >
      <div className="card-head">
        <Database size={16} />
        <h3>{pool.name}</h3>
        <Badge tone={STATUS_TONE[tone]}>{sentence(pool.status)}</Badge>
      </div>
      <div className="backup-main">
        <span className="backup-big num">{pool.pct != null ? `${Math.round(pool.pct)}%` : '—'}</span>
        <span className="small muted">
          {bytes(pool.free)} free of {bytes(pool.size)}
        </span>
      </div>
      <div className="bw-bar">
        <div className={`bw-fill pool-fill tone-${pool.pct != null && pool.pct >= 90 ? 'down' : pool.pct != null && pool.pct >= 80 ? 'degraded' : 'up'}`} style={{ width: `${pool.pct ?? 0}%` }} />
      </div>
      <span className="small muted">{scanLine(pool, now)}</span>
      {layout.length > 0 && <span className="small muted">{layout.join(' + ')}</span>}
      {disks.length > 0 && (
        <div className="temp-chips">
          {disks.map((d) => (
            <TempChip key={d.name} disk={d} />
          ))}
        </div>
      )}
      {pool.detail && pool.status !== 'ONLINE' && <p className="small warn-text">{pool.detail}</p>}
    </article>
  )
}

function alertIcon(level: string) {
  return level === 'INFO' || level === 'NOTICE' ? <Info size={14} /> : <AlertTriangle size={14} />
}

function alertTone(level: string): string {
  return level === 'INFO' || level === 'NOTICE' ? 'unknown' : level === 'WARNING' ? 'warning' : 'failed'
}

function AlertList({ alerts, now }: { alerts: NasAlert[]; now: number }) {
  return (
    <ul className="nas-alerts">
      {alerts.map((a) => (
        <li key={a.id} className={`bk-${alertTone(a.level)}`}>
          <span className="nas-alert-icon">{alertIcon(a.level)}</span>
          <span className="nas-alert-text">
            <span>{a.text}</span>
            <span className="small muted">
              {a.level.toLowerCase()} · {a.klass}
              {a.since ? ` · ${ago(a.since * 1000, now)}` : ''}
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}

export function Storage({ health }: { health: TrueNASHealth }) {
  const { open } = useApp()
  const now = useClock()
  const disks = health.disks ?? []
  const loose = disks.filter((d) => !d.pool)
  const alerts = health.alerts ?? []
  return (
    <DeckSection
      id="storage"
      title="Storage"
      aside={
        <>
          {health.system && (
            <RowButton className="small muted storage-system" onClick={() => open('nas')}>
              TrueNAS {health.system.version}
              {health.system.uptime_s != null && ` · up ${duration(health.system.uptime_s)}`}
              {health.system.update && <span className="update-text"> · update available</span>}
            </RowButton>
          )}
        </>
      }
    >
      {!health.configured && <p className="small warn-text">TrueNAS is configured but TRUENAS_API_KEY is not set.</p>}
      {health.configured && health.error && <p className="small warn-text">TrueNAS unavailable: {health.error}</p>}
      {health.configured && !health.ok && !health.error && <Empty>Reading TrueNAS…</Empty>}
      {health.ok && (
        <div className="backup-grid">
          {health.pools.map((p, i) => (
            <PoolCard key={p.name} pool={p} disks={disks.filter((d) => d.pool === p.name)} index={i} />
          ))}
          <article className="backup-card card storage-side" style={{ ['--i' as string]: health.pools.length }}>
            <div className="card-head">
              <HardDrive size={16} />
              <h3>
                <RowButton onClick={() => open('nas')}>TrueNAS</RowButton>
              </h3>
            </div>
            {alerts.length ? <AlertList alerts={alerts.slice(0, 4)} now={now} /> : <span className="small muted">No alerts.</span>}
            {loose.length > 0 && (
              <>
                <span className="small muted">Other disks</span>
                <div className="temp-chips">
                  {loose.map((d) => (
                    <TempChip key={d.name} disk={d} />
                  ))}
                </div>
              </>
            )}
          </article>
        </div>
      )}
    </DeckSection>
  )
}

function TempRange({ disk }: { disk: Disk }) {
  if (disk.temp_max_7d == null) return null
  const lo = 20
  const hi = disk.type === 'HDD' ? 60 : 85
  const at = (t: number) => `${Math.max(0, Math.min(100, ((t - lo) / (hi - lo)) * 100))}%`
  return (
    <div className="temp-range" title="Last 7 days: average and highest">
      <span className={`temp-range-fill tone-${tempTone(disk, disk.temp_max_7d)}`} style={{ left: 0, width: at(disk.temp_max_7d) }} />
      {disk.temp_avg_7d != null && <span className="temp-range-avg" style={{ left: at(disk.temp_avg_7d) }} />}
      {disk.temp != null && <span className="temp-range-now" style={{ left: at(disk.temp) }} />}
    </div>
  )
}

function DiskRow({ disk }: { disk: Disk }) {
  const { open } = useApp()
  return (
    <li>
      <RowButton onClick={() => open('disk', disk.name)}>
        <span className="mono">{disk.name}</span>
        <span className="small muted disk-model">{disk.model}</span>
        <span className={`num tone-${tempTone(disk, disk.temp)}`}>{disk.temp != null ? `${Math.round(disk.temp)} °C` : '—'}</span>
      </RowButton>
    </li>
  )
}

export function PoolDrawer({ name }: { name: string }) {
  const { snapshot } = useApp()
  const now = useClock()
  const health = snapshot?.truenas
  const pool = health?.pools.find((p) => p.name === name)
  if (!health || !pool) return <Empty>No such pool.</Empty>
  const tone = poolTone(pool)
  const disks = (health.disks ?? []).filter((d) => d.pool === pool.name)
  const datasets = (health.datasets ?? []).filter((d) => d.pool === pool.name)
  const scan = pool.scan
  return (
    <div>
      <div className="drawer-kicker">
        <Database size={14} /> Pool <Badge tone={STATUS_TONE[tone]}>{sentence(pool.status)}</Badge>
      </div>
      <h3 className="drawer-title">{pool.name}</h3>
      {pool.detail && <p className="small warn-text">{pool.detail}</p>}
      <div className="bw-bar">
        <div className="bw-fill pool-fill tone-up" style={{ width: `${pool.pct ?? 0}%` }} />
      </div>
      <Facts
        items={[
          ['Used', `${bytes(pool.allocated)} of ${bytes(pool.size)}${pool.pct != null ? ` (${pool.pct}%)` : ''}`],
          ['Free', bytes(pool.free)],
          ['Fragmentation', pool.fragmentation != null ? `${pool.fragmentation}%` : null],
          ['Healthy', pool.healthy ? 'yes' : 'no'],
          ['Last scrub', scan ? scanLine(pool, now) : 'never'],
          ['Scrub took', scan?.started && scan.finished && scan.finished > scan.started ? duration(scan.finished - scan.started) : null],
        ]}
      />
      <h4 className="drawer-sub">Layout</h4>
      <ul className="recap-list">
        {pool.vdevs.map((v, i) => (
          <li key={i}>
            <span>
              {v.role} · {v.type}
            </span>
            <span className="small muted num">
              {v.status?.toLowerCase()} · {v.disks.join(', ')}
              {v.errors ? <span className="warn-text"> · {v.errors} errors</span> : null}
            </span>
          </li>
        ))}
      </ul>
      {disks.length > 0 && (
        <>
          <h4 className="drawer-sub">Disks</h4>
          <ul className="disk-list">
            {disks.map((d) => (
              <DiskRow key={d.name} disk={d} />
            ))}
          </ul>
        </>
      )}
      {datasets.length > 0 && (
        <>
          <h4 className="drawer-sub">Datasets</h4>
          <ul className="recap-list">
            {datasets.map((d) => (
              <li key={d.name}>
                <span className="mono">{d.name}</span>
                <span className="small muted num">{bytes(d.used)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <SourceNote source="TrueNAS API (read-only key)" at={health.checked_at} />
    </div>
  )
}

export function DiskDrawer({ name }: { name: string }) {
  const { snapshot, open } = useApp()
  const health = snapshot?.truenas
  const disk = health?.disks?.find((d) => d.name === name)
  if (!health || !disk) return <Empty>No such disk.</Empty>
  const tone = tempTone(disk, disk.temp)
  return (
    <div>
      <div className="drawer-kicker">
        <Thermometer size={14} /> Disk{' '}
        <Badge tone={STATUS_TONE[tone]}>{disk.temp != null ? `${Math.round(disk.temp)} °C` : 'No reading'}</Badge>
      </div>
      <h3 className="drawer-title mono">{disk.name}</h3>
      <TempRange disk={disk} />
      <Facts
        items={[
          ['Model', disk.model],
          ['Kind', [disk.type, disk.rpm ? `${disk.rpm} rpm` : null].filter(Boolean).join(' · ') || null],
          ['Size', disk.size != null ? bytes(disk.size) : null],
          ['Temperature', disk.temp != null ? `${disk.temp.toFixed(1)} °C` : null],
          ['Last 7 days', disk.temp_max_7d != null ? `average ${disk.temp_avg_7d} °C · highest ${disk.temp_max_7d} °C` : null],
          [
            'Pool',
            disk.pool ? (
              <RowButton onClick={() => open('pool', disk.pool!)}>{disk.pool}</RowButton>
            ) : (
              'not in a data pool'
            ),
          ],
        ]}
      />
      <p className="small muted">
        TrueNAS 25.10 no longer reports SMART results through its API; a failing disk shows up as a TrueNAS alert
        instead.
      </p>
      <SourceNote source="TrueNAS API (read-only key)" at={health.checked_at} />
    </div>
  )
}

export function NasDrawer() {
  const { snapshot } = useApp()
  const now = useClock()
  const health = snapshot?.truenas
  if (!health) return <Empty>TrueNAS is not configured.</Empty>
  return (
    <div>
      <div className="drawer-kicker">
        <HardDrive size={14} /> TrueNAS
      </div>
      <h3 className="drawer-title">Storage health</h3>
      {health.error && <p className="small warn-text">{health.error}</p>}
      <Facts
        items={[
          ['Version', health.system?.version ?? null],
          ['Up for', health.system?.uptime_s != null ? duration(health.system.uptime_s) : null],
          ['Update', health.system ? (health.system.update ? 'available (see alerts)' : 'none') : null],
          ['Pools', health.pools.map((p) => `${p.name} ${p.status.toLowerCase()}`).join(' · ') || null],
        ]}
      />
      <h4 className="drawer-sub">Alerts</h4>
      {health.alerts == null ? (
        <p className="small muted">The key cannot read alerts.</p>
      ) : health.alerts.length ? (
        <AlertList alerts={health.alerts} now={now} />
      ) : (
        <Empty>No alerts.</Empty>
      )}
      {health.disks && (
        <>
          <h4 className="drawer-sub">Disks</h4>
          <ul className="disk-list">
            {health.disks.map((d) => (
              <DiskRow key={d.name} disk={d} />
            ))}
          </ul>
        </>
      )}
      {health.datasets && health.datasets.length > 0 && (
        <>
          <h4 className="drawer-sub">Datasets</h4>
          <ul className="recap-list">
            {health.datasets.map((d) => (
              <li key={d.name}>
                <span className="mono">{d.name}</span>
                <span className="small muted num">{bytes(d.used)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <SourceNote source="TrueNAS API (read-only key)" at={health.checked_at} />
    </div>
  )
}

/** One line for a machine card or drawer: the pools at a glance, pointing to
 *  the Storage section, which is the only place their details live. */
export function PoolsSummary({ pools }: { pools: Pool[] }) {
  const healthy = pools.filter((p) => poolTone(p) === 'up').length
  const fullest = [...pools].sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0))[0]
  const go = (e: MouseEvent) => {
    e.stopPropagation()
    document.getElementById('storage')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  return (
    <button type="button" className="link-btn small pools-summary" onClick={go}>
      <Database size={12} /> {healthy === pools.length ? `${pools.length} pools healthy` : `${pools.length - healthy} of ${pools.length} pools need a look`}
      {fullest?.pct != null && ` · ${fullest.name} ${Math.round(fullest.pct)}% full`} ›
    </button>
  )
}
