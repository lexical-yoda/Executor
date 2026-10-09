import { AlertTriangle, Check, CircleDashed, Loader2, Minus, Play, TerminalSquare, X } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { api, ApiError, type ActionInfo, type RunDetail, type RunSummary, type StepStatus } from '../api'
import { ago } from '../format'

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

function Modal({ children, onClose, label }: { children: ReactNode; onClose: () => void; label: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    ref.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} ref={ref}>
        {children}
      </div>
    </div>
  )
}

function ConfirmDialog({
  action,
  onCancel,
  onConfirm,
  busy,
  error,
}: {
  action: ActionInfo
  onCancel: () => void
  onConfirm: () => void
  busy: boolean
  error: string | null
}) {
  return (
    <Modal onClose={onCancel} label={`Confirm ${action.title}`}>
      <div className={`modal-head danger-${action.danger}`}>
        <AlertTriangle size={18} />
        <h3>{action.title}</h3>
      </div>
      <p>{action.description}</p>
      <p className="warn">{action.confirm}</p>
      <ol className="plan">
        {action.steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      {error && <p className="error">{error}</p>}
      <div className="modal-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className={`btn btn-${action.danger}`} onClick={onConfirm} disabled={busy}>
          {busy ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
          Run action
        </button>
      </div>
    </Modal>
  )
}

function RunConsole({ runId, onClose, onFinished }: { runId: string; onClose: () => void; onFinished: () => void }) {
  const [run, setRun] = useState<RunDetail | null>(null)
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLPreElement>(null)
  const finishedRef = useRef(onFinished)
  finishedRef.current = onFinished

  useEffect(() => {
    let offset = 0
    let timer: number | undefined
    let stopped = false
    const tick = async () => {
      try {
        const detail = await api.run(runId, offset)
        if (stopped) return
        offset = detail.next_offset
        setRun(detail)
        if (detail.lines.length) setLines((prev) => [...prev, ...detail.lines])
        setError(null)
        if (detail.status !== 'running') {
          finishedRef.current()
          return
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
      timer = window.setTimeout(tick, 800)
    }
    void tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [runId])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  const status = run?.status ?? 'running'
  return (
    <Modal onClose={onClose} label="Action output">
      <div className={`modal-head run-${status}`}>
        <TerminalSquare size={18} />
        <h3>{run?.title ?? 'Starting…'}</h3>
        <span className={`run-badge run-badge-${status}`}>{status}</span>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      {run && (
        <ol className="steps">
          {run.steps.map((s) => (
            <li key={s.name} className={`step step-${s.status}`}>
              <StepIcon status={s.status} />
              {s.name}
            </li>
          ))}
        </ol>
      )}
      <pre className="log" ref={logRef} aria-live="polite">
        {lines.join('\n') || 'Waiting for output…'}
      </pre>
      {run?.error && <p className="error">{run.error}</p>}
      {error && <p className="error">Lost contact with the run: {error}</p>}
    </Modal>
  )
}

export function Actions({ runnerOk, now }: { runnerOk: boolean; now: number }) {
  const [actions, setActions] = useState<ActionInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [busyRun, setBusyRun] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<ActionInfo | null>(null)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)

  const loadRuns = async () => {
    try {
      const data = await api.runs()
      setRuns(data.runs)
      setBusyRun(data.busy)
    } catch {
      /* the actions error below already explains an unreachable runner */
    }
  }

  useEffect(() => {
    const load = async () => {
      try {
        setActions(await api.actions())
        setLoadError(null)
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : String(err))
      }
    }
    void load()
    void loadRuns()
    const id = window.setInterval(() => {
      void load()
      void loadRuns()
    }, 15_000)
    return () => window.clearInterval(id)
  }, [runnerOk])

  const confirm = async () => {
    if (!confirming) return
    setStarting(true)
    setStartError(null)
    try {
      const run = await api.start(confirming.id)
      setConfirming(null)
      setOpenRun(run.id)
      setBusyRun(run.id)
    } catch (err) {
      setStartError(err instanceof ApiError || err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }

  return (
    <section className="section">
      <div className="section-head">
        <h2>Actions</h2>
        {busyRun && (
          <button type="button" className="link-btn" onClick={() => setOpenRun(busyRun)}>
            <Loader2 size={13} className="spin" /> An action is running
          </button>
        )}
      </div>

      {loadError && <p className="error">Runner unavailable: {loadError}</p>}

      <div className="actions">
        {actions?.map((a, i) => {
          const last = runs.find((r) => r.action === a.id)
          return (
            <article key={a.id} className={`action card danger-${a.danger}`} style={{ ['--i' as string]: i }}>
              <div className="action-body">
                <h3>{a.title}</h3>
                <p className="small muted">{a.description}</p>
                {last && (
                  <button type="button" className={`last-run run-${last.status}`} onClick={() => setOpenRun(last.id)}>
                    Last run {last.status} {ago(last.started_at, now)}
                  </button>
                )}
              </div>
              <button
                type="button"
                className={`btn btn-${a.danger}`}
                disabled={!!busyRun}
                onClick={() => {
                  setStartError(null)
                  setConfirming(a)
                }}
              >
                <Play size={15} />
                Run
              </button>
            </article>
          )
        })}
        {actions && !actions.length && <p className="muted">No actions configured.</p>}
      </div>

      {runs.length > 0 && (
        <details className="history">
          <summary>Recent runs ({runs.length})</summary>
          <ul>
            {runs.slice(0, 15).map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => setOpenRun(r.id)}>
                  <span className={`run-badge run-badge-${r.status}`}>{r.status}</span>
                  <span>{r.title}</span>
                  <span className="muted small">
                    {ago(r.started_at, now)} · {r.requested_by}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {confirming && (
        <ConfirmDialog
          action={confirming}
          busy={starting}
          error={startError}
          onCancel={() => setConfirming(null)}
          onConfirm={confirm}
        />
      )}
      {openRun && (
        <RunConsole
          key={openRun}
          runId={openRun}
          onClose={() => setOpenRun(null)}
          onFinished={() => void loadRuns()}
        />
      )}
    </section>
  )
}
