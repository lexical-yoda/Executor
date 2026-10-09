import { useCallback, useEffect, useRef, useState } from 'react'

/** Poll an async source on an interval; pauses while the tab is hidden. */
export function usePoll<T>(load: () => Promise<T>, intervalMs: number) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const loadRef = useRef(load)
  loadRef.current = load

  const refresh = useCallback(async () => {
    try {
      const value = await loadRef.current()
      setData(value)
      setError(null)
      setUpdatedAt(Date.now())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    let timer: number | undefined
    let first = true
    const tick = async () => {
      // Always load once, even in a background tab; after that, skip while hidden.
      if (first || !document.hidden) await refresh()
      first = false
      timer = window.setTimeout(tick, intervalMs)
    }
    const onVisible = () => {
      if (!document.hidden) void refresh()
    }
    void tick()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [intervalMs, refresh])

  return { data, error, updatedAt, refresh }
}

/** Current time, re-rendered every `stepMs`. */
export function useNow(stepMs = 1000) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), stepMs)
    return () => window.clearInterval(id)
  }, [stepMs])
  return now
}

/** Presentation mode blurs usernames; remembered per browser. */
export function usePresentation(): [boolean, () => void] {
  const [on, setOn] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem('executor.presenting') === '1'
    } catch {
      return false
    }
  })
  useEffect(() => {
    try {
      window.localStorage.setItem('executor.presenting', on ? '1' : '0')
    } catch {
      /* storage can be unavailable; the toggle still works for this visit */
    }
  }, [on])
  return [on, () => setOn((v) => !v)]
}

/** Whether the screen is phone sized. */
export function useNarrow(width = 760): boolean {
  const query = `(max-width: ${width}px)`
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const media = window.matchMedia(query)
    const update = () => setNarrow(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [query])
  return narrow
}
