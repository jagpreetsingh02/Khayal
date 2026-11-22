import { useCallback, useEffect, useState } from 'react'

import { api } from './api'

export const POLL_MS = 4000

interface PollState<T> {
  path: string | null
  data: T | null
  error: string | null
}

/** GET `path` now and every POLL_MS while mounted (so approvals and alerts show up live).
 * `reload()` refetches at once, e.g. after a button press. */
export function usePoll<T>(path: string | null, every = POLL_MS) {
  const [state, setState] = useState<PollState<T>>({ path: null, data: null, error: null })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!path) return
    let alive = true
    const load = async () => {
      try {
        const data = await api<T>(path)
        if (alive) setState({ path, data, error: null })
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e)
        if (alive) setState((s) => ({ path, data: s.path === path ? s.data : null, error }))
      }
    }
    void load()
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, every)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [path, every, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  const current = state.path === path
  return { data: current ? state.data : null, error: current ? state.error : null, reload }
}
