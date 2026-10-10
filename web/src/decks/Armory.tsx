import { Loader2 } from 'lucide-react'
import type { ActionInfo } from '../api'
import { ago, requester } from '../format'
import { useActions, useApp, useClock } from '../state'
import { ActionCard, RunBadge, runClock, runSeconds } from '../components/ActionKit'
import { DeckSection, Empty, RowButton } from '../components/ui'

export function Armory() {
  const { actions, error, runs, busy } = useActions()
  const { open, snapshot } = useApp()
  const now = useClock()
  const groups = new Map<string, ActionInfo[]>()
  for (const a of actions ?? []) {
    const key = a.group ?? 'General'
    groups.set(key, [...(groups.get(key) ?? []), a])
  }
  let index = 0
  return (
    <div className="deck-stack">
      {error && <p className="error">Runner unavailable: {error}</p>}
      {!actions && !error && (
        <p className="muted">
          <Loader2 size={14} className="spin" /> Loading actions…
        </p>
      )}
      {busy && (
        <RowButton className="armory-running card" onClick={() => open('run', busy)}>
          <Loader2 size={15} className="spin" />
          <span>{runs.find((r) => r.id === busy)?.title ?? 'An action'} is running</span>
          <span className="small muted">watch the sequence</span>
        </RowButton>
      )}
      {[...groups.entries()].map(([group, list]) => (
        <DeckSection key={group} title={group}>
          <div className="actions-grid">
            {list.map((a) => (
              <ActionCard key={a.id} action={a} index={index++} />
            ))}
          </div>
        </DeckSection>
      ))}
      {actions && !actions.length && <Empty>No actions configured.</Empty>}
      <DeckSection title="Recent runs">
        {runs.length ? (
          <ul className="run-list card">
            {runs.slice(0, 20).map((r) => (
              <li key={r.id}>
                <RowButton onClick={() => open('run', r.id)}>
                  <RunBadge status={r.status} />
                  <span className="run-list-title">{r.title}</span>
                  <span className="small muted">
                    {ago(r.started_at, now)} · {runClock(runSeconds(r.started_at, r.finished_at, now))} ·{' '}
                    {requester(r.requested_by, snapshot?.machines)}
                  </span>
                </RowButton>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>Nothing has been launched yet.</Empty>
        )}
      </DeckSection>
    </div>
  )
}
