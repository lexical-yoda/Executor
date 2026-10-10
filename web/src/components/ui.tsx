import { ChevronDown, ChevronRight } from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { ago } from '../format'
import { useClock } from '../state'

const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** A number that glides to each new value instead of jumping. */
export function Num({
  value,
  format = (v) => String(Math.round(v)),
  className = '',
  title,
}: {
  value: number | null | undefined
  format?: (v: number) => string
  className?: string
  title?: string
}) {
  const [shown, setShown] = useState<number | null>(value ?? null)
  const from = useRef<number | null>(value ?? null)
  const frame = useRef(0)

  useEffect(() => {
    if (value == null) {
      setShown(null)
      from.current = null
      return
    }
    const start = from.current
    if (start == null || reduceMotion() || start === value) {
      setShown(value)
      from.current = value
      return
    }
    const began = performance.now()
    const step = (t: number) => {
      const k = Math.min(1, (t - began) / 700)
      const eased = 1 - Math.pow(1 - k, 3)
      const current = start + (value - start) * eased
      setShown(current)
      from.current = current
      if (k < 1) frame.current = requestAnimationFrame(step)
    }
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame.current)
  }, [value])

  return (
    <span className={`num ${className}`} title={title}>
      {shown == null ? '—' : format(shown)}
    </span>
  )
}

/** "Source · updated" line that every drawer ends with. */
export function SourceNote({ source, at }: { source: string; at?: number | string | null }) {
  const now = useClock()
  return (
    <p className="source-note small muted">
      Source: {source}
      {at != null && ` · updated ${ago(typeof at === 'number' && at < 1e12 ? at * 1000 : at, now)}`}
    </p>
  )
}

/** A bento tile on the bridge: a header that jumps to its deck, and live content. */
export function Tile({
  title,
  subtitle,
  icon,
  onOpen,
  className = '',
  children,
  tone,
}: {
  title: string
  subtitle?: ReactNode
  icon: ReactNode
  onOpen?: () => void
  className?: string
  children: ReactNode
  tone?: 'up' | 'degraded' | 'down' | 'unknown'
}) {
  return (
    <section className={`tile-bento card ${tone ? `tone-edge-${tone}` : ''} ${className}`}>
      <header className="tile-bento-head">
        {onOpen ? (
          <button type="button" className="tile-bento-title" onClick={onOpen}>
            <span className="tile-bento-icon">{icon}</span>
            <span>{title}</span>
            <ChevronRight size={14} className="tile-bento-chevron" />
          </button>
        ) : (
          <span className="tile-bento-title">
            <span className="tile-bento-icon">{icon}</span>
            <span>{title}</span>
          </span>
        )}
        {subtitle && <span className="tile-bento-sub small muted">{subtitle}</span>}
      </header>
      <div className="tile-bento-body">{children}</div>
    </section>
  )
}

// Which sections this browser has folded away; remembered per device only.
const FOLDED_KEY = 'executor.folded'

function readFolded(): Set<string> {
  try {
    return new Set(JSON.parse(window.localStorage.getItem(FOLDED_KEY) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

function writeFolded(folded: Set<string>) {
  try {
    window.localStorage.setItem(FOLDED_KEY, JSON.stringify([...folded]))
  } catch {
    /* storage can be unavailable; folding still works for this visit */
  }
}

/** A section of a deck. Its heading folds it away (remembered on this device). */
export function DeckSection({
  title,
  aside,
  children,
  id,
}: {
  title: string
  aside?: ReactNode
  children: ReactNode
  id?: string
}) {
  const key = id ?? title.toLowerCase().replace(/[^a-z0-9]+/g, '-')
  const [folded, setFolded] = useState(() => readFolded().has(key))
  const toggle = () => {
    const all = readFolded()
    if (folded) all.delete(key)
    else all.add(key)
    writeFolded(all)
    setFolded(!folded)
  }
  return (
    <section className={`section${folded ? ' section-folded' : ''}`} id={id}>
      <div className="section-head">
        <h2>
          <button type="button" className="section-toggle" onClick={toggle} aria-expanded={!folded}>
            <ChevronDown size={14} className="section-chevron" aria-hidden="true" />
            {title}
          </button>
        </h2>
        {!folded && aside}
      </div>
      {!folded && children}
    </section>
  )
}

/** Key and value pairs in a drawer. */
export function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {items
        .filter(([, v]) => v !== null && v !== undefined && v !== '')
        .map(([k, v]) => (
          <div key={k} className="fact">
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
    </dl>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty-state small muted">{children}</p>
}

/** Clickable row inside cards and drawers. */
export function RowButton({
  onClick,
  children,
  className = '',
  title,
}: {
  onClick: () => void
  children: ReactNode
  className?: string
  title?: string
}) {
  return (
    <button type="button" className={`row-btn ${className}`} onClick={onClick} title={title}>
      {children}
    </button>
  )
}
