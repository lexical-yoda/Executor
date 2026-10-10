import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { collectAlerts } from './alerts'
import { api } from './api'
import { DrawerHost as Drawer } from './components/Drawer'
import { Guard } from './components/Guard'
import { AlertRow, Header, Tabs } from './components/Header'
import { Archives } from './decks/Archives'
import { Armory } from './decks/Armory'
import { Bridge } from './decks/Bridge'
import { Engineering } from './decks/Engineering'
import { Hangar } from './decks/Hangar'
import { Holonet } from './decks/Holonet'
import { Settings } from './decks/Settings'
import { useNarrow, usePoll, usePresentation } from './hooks'
import { type Deck, DECK_INFO, DECKS } from './route'
import { ActionsProvider, AppProvider, ClockProvider, useApp } from './state'

// The demo tour: how long it lingers on each deck.
const TOUR: { deck: Deck; ms: number }[] = [
  { deck: 'bridge', ms: 26_000 },
  { deck: 'hangar', ms: 10_000 },
  { deck: 'holonet', ms: 22_000 },
  { deck: 'engineering', ms: 14_000 },
  { deck: 'archives', ms: 10_000 },
  { deck: 'armory', ms: 10_000 },
]

function Shell() {
  const { snapshot, error, stale, route, showDeck, open, go } = useApp()
  const alerts = useMemo(() => collectAlerts(snapshot), [snapshot])
  const [presenting, togglePresenting] = usePresentation()
  const [attract, setAttract] = useState(false)
  const narrow = useNarrow()
  const tourStep = useRef(0)

  useEffect(() => {
    document.documentElement.classList.toggle('presenting', presenting || attract)
  }, [presenting, attract])

  useEffect(() => {
    if (snapshot?.site.title) document.title = snapshot.site.title
  }, [snapshot?.site.title])

  // Number keys switch decks; p toggles presentation mode.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (e.metaKey || e.ctrlKey || e.altKey || target?.closest('input, textarea, select, [contenteditable]')) return
      const deck = DECKS.find((d) => DECK_INFO[d].key === e.key)
      if (deck) showDeck(deck)
      else if (e.key === 'p') togglePresenting()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [showDeck, togglePresenting])

  // Attract mode: cycle the decks with names blurred until someone touches anything.
  const stopTour = useCallback(() => setAttract(false), [])
  useEffect(() => {
    if (!attract) return
    tourStep.current = 0
    go({ deck: TOUR[0].deck, drawer: null })
    let timer = 0
    const next = () => {
      tourStep.current = (tourStep.current + 1) % TOUR.length
      go({ deck: TOUR[tourStep.current].deck, drawer: null })
      timer = window.setTimeout(next, TOUR[tourStep.current].ms)
    }
    timer = window.setTimeout(next, TOUR[0].ms)
    const stop = () => stopTour()
    // Let the click that started the tour finish first.
    const arm = window.setTimeout(() => {
      for (const ev of ['keydown', 'pointerdown', 'wheel', 'touchstart']) window.addEventListener(ev, stop, { once: true })
    }, 400)
    return () => {
      window.clearTimeout(timer)
      window.clearTimeout(arm)
      for (const ev of ['keydown', 'pointerdown', 'wheel', 'touchstart']) window.removeEventListener(ev, stop)
    }
  }, [attract, go, stopTour])

  const deck = route.deck
  return (
    <div className={`app${stale || (error && snapshot) ? ' stale' : ''}${attract ? ' touring' : ''}${narrow ? ' narrow' : ''}`}>
      <div className="starfield" aria-hidden="true" />
      <Header
        alerts={alerts}
        presenting={presenting}
        togglePresenting={togglePresenting}
        attract={attract}
        toggleAttract={() => setAttract((v) => !v)}
      />
      {!narrow && <Tabs alerts={alerts} />}
      <AlertRow alerts={alerts} />
      <main className={`layout deck-${deck}`} key={deck}>
        {snapshot ? (
          <Guard key={deck} label={DECK_INFO[deck].name}>
            {deck === 'bridge' && <Bridge tour={attract} />}
            {deck === 'hangar' && <Hangar />}
            {deck === 'engineering' && <Engineering />}
            {deck === 'holonet' && <Holonet tour={attract} />}
            {deck === 'archives' && <Archives />}
            {deck === 'armory' && <Armory />}
            {deck === 'settings' && <Settings />}
          </Guard>
        ) : (
          <div className="loading">
            <span className="loader" />
            <p className="muted">{error ? `Cannot load status: ${error}` : 'Establishing link…'}</p>
          </div>
        )}
      </main>
      <Guard key={route.drawer ? `${route.drawer.kind}:${route.drawer.id}` : 'none'} label="This view">
        <Drawer />
      </Guard>
      {attract && (
        <div className="tour-banner" role="status">
          Demo tour · {DECK_INFO[deck].name} · touch anything to take the helm
        </div>
      )}
      <footer className="footer muted small">
        Executor · WireGuard only ·{' '}
        <button type="button" className="link-btn small" onClick={() => open('feed')}>
          ship's log
        </button>
      </footer>
      {narrow && <Tabs alerts={alerts} bottom />}
    </div>
  )
}

// Status every 5 s on a Mac; a phone checks every 15 s, which is plenty to
// glance at and spares its battery and radio.
const POLL_MS = window.matchMedia('(pointer: coarse)').matches ? 15_000 : 5_000

export default function App() {
  const { data: snapshot, error, updatedAt, refresh } = usePoll(api.status, POLL_MS)
  return (
    <ClockProvider>
      <AppProvider snapshot={snapshot} error={error} updatedAt={updatedAt} staleAfter={Math.max(20_000, POLL_MS * 3)} reload={refresh}>
        <ActionsProvider runnerOk={snapshot?.runner.ok ?? false}>
          <Shell />
        </ActionsProvider>
      </AppProvider>
    </ClockProvider>
  )
}
