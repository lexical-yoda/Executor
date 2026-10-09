import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { type ActionInfo, api, ApiError, type RunSummary, type Snapshot } from './api'
import { type Deck, type DrawerRef, type Route, useRoute } from './route'

interface AppState {
  snapshot: Snapshot | null
  error: string | null
  updatedAt: number | null
  now: number
  /** True when the last good update is too old to trust. */
  stale: boolean
  route: Route
  go: (route: Route, replace?: boolean) => void
  showDeck: (deck: Deck) => void
  open: (kind: string, id?: string, deck?: Deck) => void
  openRef: (ref: DrawerRef | null, deck?: Deck) => void
  close: () => void
}

const AppContext = createContext<AppState | null>(null)

export function useApp(): AppState {
  const value = useContext(AppContext)
  if (!value) throw new Error('useApp outside AppProvider')
  return value
}

export function AppProvider({
  snapshot,
  error,
  updatedAt,
  now,
  children,
}: {
  snapshot: Snapshot | null
  error: string | null
  updatedAt: number | null
  now: number
  children: ReactNode
}) {
  const { route, go } = useRoute()
  const routeRef = useRef(route)
  routeRef.current = route

  const showDeck = useCallback((deck: Deck) => go({ deck, drawer: null }), [go])
  const open = useCallback(
    (kind: string, id = '', deck?: Deck) => go({ deck: deck ?? routeRef.current.deck, drawer: { kind, id } }),
    [go],
  )
  const openRef = useCallback(
    (ref: DrawerRef | null, deck?: Deck) => ref && go({ deck: deck ?? routeRef.current.deck, drawer: ref }),
    [go],
  )
  const close = useCallback(() => go({ deck: routeRef.current.deck, drawer: null }), [go])
  const stale = updatedAt !== null && now - updatedAt > 20_000

  const value = useMemo(
    () => ({ snapshot, error, updatedAt, now, stale, route, go, showDeck, open, openRef, close }),
    [snapshot, error, updatedAt, now, stale, route, go, showDeck, open, openRef, close],
  )
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

// --- actions ----------------------------------------------------------------

interface ActionsState {
  actions: ActionInfo[] | null
  error: string | null
  runs: RunSummary[]
  busy: string | null
  refresh: () => void
  /** Start an action; resolves to the new run's id. */
  start: (id: string) => Promise<string>
  attachedTo: (target: string) => ActionInfo[]
}

const ActionsContext = createContext<ActionsState | null>(null)

export function useActions(): ActionsState {
  const value = useContext(ActionsContext)
  if (!value) throw new Error('useActions outside ActionsProvider')
  return value
}

export function ActionsProvider({ runnerOk, children }: { runnerOk: boolean; children: ReactNode }) {
  const [actions, setActions] = useState<ActionInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  const loadRuns = useCallback(async () => {
    try {
      const data = await api.runs()
      setRuns(data.runs)
      setBusy(data.busy)
    } catch {
      /* the actions error explains an unreachable runner */
    }
  }, [])

  useEffect(() => {
    let stopped = false
    const load = async () => {
      try {
        const list = await api.actions()
        if (!stopped) {
          setActions(list)
          setError(null)
        }
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err))
      }
    }
    void load()
    const id = window.setInterval(() => !document.hidden && void load(), 60_000)
    return () => {
      stopped = true
      window.clearInterval(id)
    }
  }, [runnerOk, tick])

  useEffect(() => {
    void loadRuns()
    // Poll faster while something runs, so the pipeline and history stay current.
    const id = window.setInterval(() => !document.hidden && void loadRuns(), busy ? 2_000 : 15_000)
    return () => window.clearInterval(id)
  }, [busy, loadRuns, tick])

  const start = useCallback(
    async (id: string) => {
      try {
        const run = await api.start(id)
        setBusy(run.id)
        void loadRuns()
        return run.id
      } catch (err) {
        throw err instanceof ApiError || err instanceof Error ? err : new Error(String(err))
      }
    },
    [loadRuns],
  )

  const attachedTo = useCallback((target: string) => (actions ?? []).filter((a) => (a.attach ?? []).includes(target)), [actions])

  const value = useMemo(
    () => ({ actions, error, runs, busy, refresh: () => setTick((t) => t + 1), start, attachedTo }),
    [actions, error, runs, busy, start, attachedTo],
  )
  return <ActionsContext.Provider value={value}>{children}</ActionsContext.Provider>
}
