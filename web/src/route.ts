import { useCallback, useEffect, useState } from 'react'

// The page's location lives in the hash, so refresh and bookmarks land on the
// same deck with the same details open: #/deck or #/deck/kind/id.

export const DECKS = ['bridge', 'engineering', 'holonet', 'archives', 'armory'] as const
export type Deck = (typeof DECKS)[number]

export const DECK_INFO: Record<Deck, { name: string; plain: string; key: string }> = {
  bridge: { name: 'Bridge', plain: 'Overview', key: '1' },
  engineering: { name: 'Engineering', plain: 'Systems', key: '2' },
  holonet: { name: 'Holonet', plain: 'Media', key: '3' },
  archives: { name: 'Archives', plain: 'Backups', key: '4' },
  armory: { name: 'Armory', plain: 'Controls', key: '5' },
}

export interface DrawerRef {
  kind: string
  id: string
}

export interface Route {
  deck: Deck
  drawer: DrawerRef | null
}

export function parseHash(hash: string): Route {
  const [deckPart, kind, ...rest] = hash.replace(/^#\/?/, '').split('/')
  const deck = (DECKS as readonly string[]).includes(deckPart) ? (deckPart as Deck) : 'bridge'
  let drawer: DrawerRef | null = null
  if (kind) {
    try {
      drawer = { kind: decodeURIComponent(kind), id: decodeURIComponent(rest.join('/')) }
    } catch {
      drawer = null
    }
  }
  return { deck, drawer }
}

export function formatHash(route: Route): string {
  const base = `#/${route.deck}`
  if (!route.drawer) return base
  return `${base}/${encodeURIComponent(route.drawer.kind)}/${encodeURIComponent(route.drawer.id)}`
}

/** "service:sonarr" style references, as events and alerts carry them. */
export function parseRef(ref: string | null | undefined): DrawerRef | null {
  if (!ref) return null
  const at = ref.indexOf(':')
  if (at < 1) return { kind: ref, id: '' }
  return { kind: ref.slice(0, at), id: ref.slice(at + 1) }
}

export function useRoute() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const go = useCallback((next: Route, replace = false) => {
    const hash = formatHash(next)
    if (hash === window.location.hash) return
    const swap = () => {
      if (replace) window.history.replaceState(null, '', hash)
      else window.history.pushState(null, '', hash)
      setRoute(next)
    }
    // Deck changes glide where the browser supports view transitions.
    const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown }
    const deckChange = next.deck !== parseHash(window.location.hash).deck
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (deckChange && doc.startViewTransition && !reduced) doc.startViewTransition(swap)
    else swap()
  }, [])

  return { route, go }
}
