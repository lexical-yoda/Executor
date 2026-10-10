import { Eye, EyeOff, Play, Settings2, Square } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { Alert } from '../alerts'
import { statusLine } from '../alerts'
import { ago } from '../format'
import { DECK_INFO, DECKS } from '../route'
import { useApp, useClock } from '../state'
import { RunningChip } from './ActionKit'

function Meter({
  label,
  value,
  total,
  known = true,
  note,
}: {
  label: string
  value: number
  total: number
  known?: boolean
  note?: string
}) {
  const ratio = known && total ? value / total : 0
  const tone = !known ? 'unknown' : ratio === 1 ? 'up' : ratio >= 0.8 ? 'degraded' : 'down'
  return (
    <div className={`meter meter-${tone}`} title={`${label}: ${known ? `${value} of ${total}` : 'unknown'}${note ? ` · ${note}` : ''}`}>
      <svg viewBox="0 0 36 36" className="meter-ring" aria-hidden="true">
        <circle cx="18" cy="18" r="15.5" className="meter-track" />
        <circle cx="18" cy="18" r="15.5" className="meter-value" strokeDasharray={`${(ratio * 97.4).toFixed(1)} 97.4`} />
      </svg>
      <div className="meter-text">
        <span className="meter-number">{known ? `${value}/${total}` : '—'}</span>
        <span className="meter-label">{label}</span>
      </div>
    </div>
  )
}

export function Header({
  alerts,
  presenting,
  togglePresenting,
  attract,
  toggleAttract,
}: {
  alerts: Alert[]
  presenting: boolean
  togglePresenting: () => void
  attract: boolean
  toggleAttract: () => void
}) {
  const { snapshot, error, updatedAt, stale, open, route, showDeck } = useApp()
  const now = useClock()
  const s = snapshot?.summary
  const connected = !!snapshot && !error && !stale
  const line = snapshot ? statusLine(alerts, connected) : { tone: 'unknown' as const, text: error ? 'Cannot reach Executor' : 'Establishing link…' }

  // The drawer opens below the header on a wide screen; it needs the header's height.
  const bar = useRef<HTMLElement>(null)
  useEffect(() => {
    const el = bar.current
    if (!el) return
    const sized = new ResizeObserver(() => document.documentElement.style.setProperty('--header-h', `${el.offsetHeight}px`))
    sized.observe(el)
    return () => sized.disconnect()
  }, [])

  return (
    <header className="header" ref={bar}>
      <div className="brand">
        <svg viewBox="0 0 32 32" className="brand-mark" aria-hidden="true">
          <path d="M16 3 29 27H3Z" />
          <path d="M10.5 21h11M12.5 16.5h7" />
        </svg>
        <div>
          <h1>{snapshot?.site.title ?? 'Executor'}</h1>
          <p className="brand-sub">{snapshot?.site.subtitle ?? 'Command deck'}</p>
        </div>
      </div>

      <button type="button" className={`headline headline-${line.tone}`} aria-live="polite" onClick={() => open('alerts')}>
        <span className="headline-glow" />
        <span className="headline-text">{line.text}</span>
        <span className={`live ${!connected ? 'live-off' : ''}`}>
          <span className="live-dot" />
          {error && updatedAt
            ? `last contact ${ago(updatedAt, now)}`
            : stale && updatedAt
              ? `last contact ${ago(updatedAt, now)}`
              : updatedAt
                ? `updated ${ago(updatedAt, now)}`
                : 'waiting'}
        </span>
      </button>

      <div className="header-side">
        <RunningChip />
        <div className="meters">
          <Meter label="Services" value={s?.services_up ?? 0} total={s?.services_total ?? 0} known={!!s} />
          <Meter
            label="Machines"
            value={s?.machines_up ?? 0}
            total={s?.machines_total ?? 0}
            known={!!s}
            note={s?.machines_away ? `${s.machines_away} away` : undefined}
          />
          <Meter label="Containers" value={s?.containers_running ?? 0} total={s?.containers_total ?? 0} known={!!s?.containers_known} />
        </div>
        {snapshot?.editable && (
          <button
            type="button"
            className={`icon-btn${route.deck === 'settings' ? ' present-on' : ''}`}
            onClick={() => showDeck(route.deck === 'settings' ? 'bridge' : 'settings')}
            aria-pressed={route.deck === 'settings'}
            title="Settings: names, groups and links of services"
          >
            <Settings2 size={16} />
          </button>
        )}
        <button
          type="button"
          className={`icon-btn${attract ? ' present-on' : ''}`}
          onClick={toggleAttract}
          aria-pressed={attract}
          title={attract ? 'Stop the demo tour' : 'Demo tour: cycle the decks with names blurred'}
        >
          {attract ? <Square size={15} /> : <Play size={16} />}
        </button>
        <button
          type="button"
          className={`icon-btn present-btn${presenting ? ' present-on' : ''}`}
          onClick={togglePresenting}
          aria-pressed={presenting}
          title={presenting ? 'Presentation mode on: names are blurred' : 'Presentation mode: blur names'}
        >
          {presenting ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
    </header>
  )
}

export function Tabs({ alerts, bottom = false }: { alerts: Alert[]; bottom?: boolean }) {
  const { route, showDeck } = useApp()
  // Sticky things below the top tabs (a deck's jump bar) need their height;
  // the phone's tab bar sits at the bottom and takes none.
  const bar = useRef<HTMLElement>(null)
  useEffect(() => {
    const el = bar.current
    const set = () => document.documentElement.style.setProperty('--tabs-h', bottom || !el ? '0px' : `${el.offsetHeight}px`)
    set()
    if (bottom || !el) return
    const sized = new ResizeObserver(set)
    sized.observe(el)
    return () => sized.disconnect()
  }, [bottom])
  return (
    <nav className={bottom ? 'tabbar' : 'tabs'} aria-label="Decks" ref={bar}>
      {DECKS.map((deck) => {
        const info = DECK_INFO[deck]
        const mine = alerts.filter((a) => a.deck === deck)
        const tone = mine.some((a) => a.level === 'bad') ? 'down' : mine.length ? 'degraded' : null
        const active = route.deck === deck
        return (
          <button
            key={deck}
            type="button"
            className={`tab${active ? ' tab-active' : ''}`}
            aria-current={active ? 'page' : undefined}
            onClick={() => showDeck(deck)}
            title={`${info.name} · ${info.plain} (${info.key})`}
          >
            <span className="tab-name">{info.name}</span>
            <span className="tab-plain">{info.plain}</span>
            {tone && <span className={`tab-dot dot-${tone}`} aria-label={`${mine.length} alerts`} />}
          </button>
        )
      })}
    </nav>
  )
}

export function AlertRow({ alerts }: { alerts: Alert[] }) {
  const { openRef, showDeck, open } = useApp()
  if (!alerts.length) return null
  const shown = alerts.slice(0, 4)
  return (
    <div className="alert-row" role="status">
      {shown.map((a) => (
        <button
          key={a.key}
          type="button"
          className={`alert-chip alert-${a.level}`}
          onClick={() => (a.ref ? openRef(a.ref, a.deck) : showDeck(a.deck))}
          title={a.detail ?? undefined}
        >
          <span className={`dot dot-${a.level === 'bad' ? 'down' : 'degraded'}`} />
          {a.title}
        </button>
      ))}
      {alerts.length > shown.length && (
        <button type="button" className="alert-chip alert-more" onClick={() => open('alerts')}>
          +{alerts.length - shown.length} more
        </button>
      )}
    </div>
  )
}
