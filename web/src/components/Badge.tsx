import type { ReactNode } from 'react'
import type { Status } from '../api'

/** The one badge every status, state and tag on the page wears: sentence case,
 *  one size, and a tone that means the same thing everywhere. */
export type Tone = 'good' | 'warn' | 'bad' | 'info' | 'accent' | 'neutral'

export const STATUS_TONE: Record<Status, Tone> = { up: 'good', degraded: 'warn', down: 'bad', unknown: 'neutral' }

export function Badge({
  tone = 'neutral',
  dot = false,
  title,
  children,
}: {
  tone?: Tone
  /** A small dot before the words, for live states (online, down). */
  dot?: boolean
  title?: string
  children: ReactNode
}) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {dot && <span className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  )
}

/** "ONLINE" or "online" from a source, as the page says it: "Online". */
export function sentence(word: string): string {
  const lower = word.toLowerCase()
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}
