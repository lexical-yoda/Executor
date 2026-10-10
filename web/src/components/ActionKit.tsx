import {
  AlertTriangle,
  Check,
  CircleDashed,
  Loader2,
  Minus,
  Pause,
  Rocket,
  ShieldAlert,
  TerminalSquare,
  Tv,
  X,
  Zap,
} from 'lucide-react'
import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react'
import { type ActionInfo, api, type RunDetail, type RunSummary, type StepStatus, type Streams } from '../api'
import { ago, requester } from '../format'
import { useNow } from '../hooks'
import { useActions, useApp, useClock } from '../state'
import { Empty } from './ui'

const HOLD_MS: Record<ActionInfo['danger'], number> = { low: 700, medium: 1400, high: 2600 }
const DANGER_LABEL: Record<ActionInfo['danger'], string> = { low: 'Routine', medium: 'Disruptive', high: 'Dangerous' }

function seconds(from: string | null, to: string | null, now: number): number | null {
  if (!from) return null
  const end = to ? Date.parse(to) : now
  return Math.max(0, Math.round((end - Date.parse(from)) / 1000))
}

function clock(total: number | null): string {
  if (total == null) return ''
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/** Press and hold to launch: holding is the confirmation, longer for riskier actions. */
export function LaunchButton({
  action,
  disabled,
  onLaunch,
}: {
  action: ActionInfo
  disabled?: boolean
  onLaunch: () => void
}) {
  const [progress, setProgress] = useState(0)
  const [launched, setLaunched] = useState(false)
  const holding = useRef(false)
  const frame = useRef(0)
  const started = useRef(0)
  const hold = HOLD_MS[action.danger]

  const tick = useCallback(
    (t: number) => {
      if (holding.current) {
        const k = Math.min(1, (t - started.current) / hold)
        setProgress(k)
        if (k >= 1) {
          holding.current = false
          setLaunched(true)
          onLaunch()
          return
        }
      } else {
        // Let go early: the ring drains back.
        let done = false
        setProgress((p) => {
          const next = Math.max(0, p - 0.06)
          done = next === 0
          return next
        })
        if (done) return
      }
      frame.current = requestAnimationFrame(tick)
    },
    [hold, onLaunch],
  )

  const begin = () => {
    if (disabled || launched) return
    holding.current = true
    started.current = performance.now() - progress * hold
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(tick)
  }
  const end = () => {
    if (!holding.current) return
    holding.current = false
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(tick)
  }
  useEffect(() => () => cancelAnimationFrame(frame.current), [])

  const onKey = (e: KeyboardEvent, down: boolean) => {
    if (e.key !== ' ' && e.key !== 'Enter') return
    e.preventDefault()
    if (down && !e.repeat) begin()
    if (!down) end()
  }

  const r = 22
  const c = 2 * Math.PI * r
  return (
    <button
      type="button"
      className={`launch launch-${action.danger}${progress > 0 ? ' launch-arming' : ''}${launched ? ' launch-done' : ''}`}
      disabled={disabled}
      onPointerDown={begin}
      onPointerUp={end}
      onPointerLeave={end}
      onPointerCancel={end}
      onKeyDown={(e) => onKey(e, true)}
      onKeyUp={(e) => onKey(e, false)}
      onContextMenu={(e) => e.preventDefault()}
      aria-label={`Hold to launch ${action.title}`}
    >
      <svg viewBox="0 0 52 52" className="launch-ring" aria-hidden="true">
        <circle cx="26" cy="26" r={r} className="launch-track" />
        <circle
          cx="26"
          cy="26"
          r={r}
          className="launch-fill"
          strokeDasharray={`${(progress * c).toFixed(1)} ${c.toFixed(1)}`}
        />
      </svg>
      <span className="launch-icon">{launched ? <Loader2 size={18} className="spin" /> : <Rocket size={18} />}</span>
      <span className="launch-text">
        {launched ? 'Launching…' : progress > 0 ? 'Keep holding…' : 'Hold to launch'}
        <span className="small launch-sub">
          {launched ? '' : `${(hold / 1000).toFixed(1)}s · ${DANGER_LABEL[action.danger].toLowerCase()}`}
        </span>
      </span>
    </button>
  )
}

function ActiveStreams() {
  const [data, setData] = useState<Streams | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let stopped = false
    api
      .streams()
      .then((d) => !stopped && setData(d))
      .catch((err) => !stopped && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      stopped = true
    }
  }, [])
  if (error || (data && data.configured && !data.ok)) {
    return <p className="streams-note warn">Could not check for active streams: {error ?? data?.error}</p>
  }
  if (!data) {
    return (
      <p className="streams-note muted">
        <Loader2 size={13} className="spin" /> Checking for active streams…
      </p>
    )
  }
  if (!data.configured) return null
  if (!data.streams.length) {
    return (
      <p className="streams-note ok">
        <Tv size={14} /> No one is watching right now.
      </p>
    )
  }
  const n = data.streams.length
  return (
    <div className="streams">
      <p className="streams-note warn">
        <Tv size={14} /> {n} active {n === 1 ? 'stream' : 'streams'} will be interrupted:
      </p>
      <ul>
        {data.streams.map((s, i) => (
          <li key={`${s.user}-${i}`}>
            <span className="stream-user user-name">{s.user}</span>
            <span className="stream-title">{s.title}</span>
            <span className="stream-meta muted small">
              {s.paused && <Pause size={11} aria-label="paused" />}
              {[s.client, s.transcoding ? 'transcoding' : null].filter(Boolean).join(' · ')}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function DangerBadge({ danger }: { danger: ActionInfo['danger'] }) {
  return <span className={`danger-badge danger-${danger}`}>{DANGER_LABEL[danger]}</span>
}

/** The pre-launch briefing and the hold-to-launch button. */
export function ActionDrawer({ id }: { id: string }) {
  const { actions, runs, busy, start, error: loadError } = useActions()
  const { open } = useApp()
  const now = useClock()
  const [error, setError] = useState<string | null>(null)
  const action = actions?.find((a) => a.id === id)
  if (!actions) return <p className="muted small">{loadError ? `Runner unavailable: ${loadError}` : 'Loading…'}</p>
  if (!action) return <Empty>This action no longer exists.</Empty>
  const history = runs.filter((r) => r.action === action.id).slice(0, 6)

  const launch = async () => {
    setError(null)
    try {
      const runId = await start(action.id)
      open('run', runId)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className={`action-brief danger-${action.danger}`}>
      <div className="drawer-kicker">
        <Zap size={14} /> Action {action.group && <span className="muted">· {action.group}</span>}
        <DangerBadge danger={action.danger} />
      </div>
      <h3 className="drawer-title">{action.title}</h3>
      <p>{action.description}</p>
      <p className="brief-warn">
        {action.danger === 'high' ? <ShieldAlert size={15} /> : <AlertTriangle size={15} />}
        {action.confirm}
      </p>
      {action.show_streams && <ActiveStreams />}
      <h4 className="drawer-sub">Sequence</h4>
      <ol className="plan">
        {action.steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      {error && <p className="error">{error}</p>}
      {busy ? (
        <button type="button" className="btn btn-ghost" onClick={() => open('run', busy)}>
          <Loader2 size={15} className="spin" /> Another action is running · watch it
        </button>
      ) : (
        <LaunchButton key={action.id} action={action} onLaunch={launch} />
      )}
      {history.length > 0 && (
        <>
          <h4 className="drawer-sub">Recent runs</h4>
          <ul className="run-list">
            {history.map((r) => (
              <li key={r.id}>
                <button type="button" className="row-btn" onClick={() => open('run', r.id)}>
                  <span className={`run-badge run-badge-${r.status}`}>{r.status}</span>
                  <span className="muted small">{ago(r.started_at, now)}</span>
                  <span className="muted small num">{clock(seconds(r.started_at, r.finished_at, now))}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function StepIcon({ status }: { status: StepStatus }) {
  switch (status) {
    case 'running':
      return <Loader2 size={15} className="spin" aria-label="running" />
    case 'succeeded':
      return <Check size={15} aria-label="done" />
    case 'failed':
      return <X size={15} aria-label="failed" />
    case 'skipped':
      return <Minus size={15} aria-label="skipped" />
    default:
      return <CircleDashed size={15} aria-label="pending" />
  }
}

/** A run as a pipeline: each step lights up as it goes, with its time. */
export function RunDrawer({ id }: { id: string }) {
  const { refresh } = useActions()
  const { snapshot } = useApp()
  const [run, setRun] = useState<RunDetail | null>(null)
  // Step timers count seconds while the run goes on; a finished run sits still.
  const now = useNow(run && !run.finished_at ? 1000 : 60_000)
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showLog, setShowLog] = useState(false)
  const logRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    let offset = 0
    let timer: number | undefined
    let stopped = false
    setLines([])
    setRun(null)
    const tick = async () => {
      try {
        const detail = await api.run(id, offset)
        if (stopped) return
        offset = detail.next_offset
        setRun(detail)
        if (detail.lines.length) setLines((prev) => [...prev, ...detail.lines])
        setError(null)
        if (detail.status !== 'running') {
          if (detail.status === 'failed') setShowLog(true)
          refresh()
          return
        }
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err))
      }
      timer = window.setTimeout(tick, 800)
    }
    void tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, showLog])

  if (!run) {
    return (
      <p className="muted small">
        {error ? `Could not load this run: ${error}` : <Loader2 size={14} className="spin" />}
      </p>
    )
  }
  const total = seconds(run.started_at, run.finished_at, now)
  const done = run.steps.filter((s) => s.status === 'succeeded').length
  return (
    <div className={`run-view run-${run.status}`}>
      <div className="drawer-kicker">
        <TerminalSquare size={14} /> Run · {ago(run.started_at, now)}
        <span className={`run-badge run-badge-${run.status}`}>{run.status}</span>
      </div>
      <h3 className="drawer-title">{run.title}</h3>
      <div className="run-meter">
        <div className="run-meter-fill" style={{ width: `${(done / Math.max(1, run.steps.length)) * 100}%` }} />
      </div>
      <p className="small muted num">
        {done} of {run.steps.length} steps · {clock(total)} {run.status === 'running' ? 'elapsed' : 'total'}
      </p>
      <ol className="pipeline">
        {run.steps.map((s) => (
          <li key={s.name} className={`pipe-step pipe-${s.status}`}>
            <span className="pipe-node">
              <StepIcon status={s.status} />
            </span>
            <span className="pipe-name">{s.name}</span>
            <span className="pipe-time small muted num">
              {s.started_at ? clock(seconds(s.started_at, s.finished_at, now)) : ''}
            </span>
          </li>
        ))}
      </ol>
      {run.status === 'succeeded' && (
        <div className="run-banner run-banner-ok">
          <Check size={16} /> Sequence complete
        </div>
      )}
      {run.status === 'failed' && (
        <div className="run-banner run-banner-bad">
          <X size={16} /> {run.error ?? 'The run failed'}
        </div>
      )}
      <button type="button" className="link-btn small" onClick={() => setShowLog((v) => !v)}>
        {showLog ? 'Hide output' : `Show output (${lines.length} lines)`}
      </button>
      {showLog && (
        <pre className="log" ref={logRef} aria-live="polite">
          {lines.join('\n') || 'Waiting for output…'}
        </pre>
      )}
      {error && <p className="error">Lost contact with the run: {error}</p>}
      <p className="small muted">Requested from {requester(run.requested_by, snapshot?.machines)}</p>
    </div>
  )
}

/** Small launch chips for the actions tied to a machine, service or panel. */
export function AttachedActions({ target, label = true }: { target: string; label?: boolean }) {
  const { attachedTo } = useActions()
  const { open } = useApp()
  const list = attachedTo(target)
  if (!list.length) return null
  return (
    <div className="attached">
      {label && <span className="small muted">Actions</span>}
      {list.map((a) => (
        <button
          key={a.id}
          type="button"
          className={`action-chip danger-${a.danger}`}
          onClick={(e) => {
            e.stopPropagation()
            open('action', a.id)
          }}
        >
          <Zap size={12} /> {a.title}
        </button>
      ))}
    </div>
  )
}

/** Header chip while an action runs, from any deck. */
export function RunningChip() {
  const { busy, runs } = useActions()
  const { open } = useApp()
  if (!busy) return null
  return <RunningClock busy={busy} runs={runs} open={open} />
}

/** The chip itself, with a seconds clock that only ticks while a run is on. */
function RunningClock({ busy, runs, open }: { busy: string; runs: RunSummary[]; open: (kind: string, id?: string) => void }) {
  const now = useNow(1000)
  const run = runs.find((r) => r.id === busy)
  return (
    <button type="button" className="running-chip" onClick={() => open('run', busy)}>
      <Loader2 size={13} className="spin" />
      <span className="running-title">{run?.title ?? 'Action running'}</span>
      <span className="num small">{run ? clock(seconds(run.started_at, null, now)) : ''}</span>
    </button>
  )
}

export function ActionCard({ action, index }: { action: ActionInfo; index: number }) {
  const { runs } = useActions()
  const { open } = useApp()
  const now = useClock()
  const last = runs.find((r) => r.action === action.id)
  return (
    <button
      type="button"
      className={`action-card card danger-${action.danger}`}
      style={{ ['--i' as string]: index }}
      onClick={() => open('action', action.id)}
    >
      <span className="action-card-top">
        <span className={`action-glyph danger-${action.danger}`}>
          <Zap size={16} />
        </span>
        <DangerBadge danger={action.danger} />
      </span>
      <span className="action-card-title">{action.title}</span>
      <span className="small muted action-card-desc">{action.description}</span>
      <span className="action-card-foot small">
        <span className="muted">
          {action.steps.length} {action.steps.length === 1 ? 'step' : 'steps'}
        </span>
        {last ? (
          <span className={`run-dot run-${last.status}`}>
            {last.status} {ago(last.started_at, now)}
          </span>
        ) : (
          <span className="muted">never run</span>
        )}
      </span>
    </button>
  )
}

export { clock as runClock, seconds as runSeconds }
