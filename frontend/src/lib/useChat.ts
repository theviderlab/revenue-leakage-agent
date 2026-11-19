import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import { createThread, decide, getThread, sendMessage } from './client'
import { loadThreadId, saveThreadId } from './storage'
import type { ApiEvent, Decision, ThreadState, TurnResponse } from './types'

export interface Chat {
  loading: boolean
  busy: boolean
  events: ApiEvent[]
  state: ThreadState | null
  // The user message of a request still in flight
  pendingText: string | null
  error: string | null
  send: (text: string) => Promise<boolean>
  decide: (interruptId: string, decision: Decision) => Promise<void>
  // Reload the thread from the server (after the settings changed, for instance)
  refresh: () => Promise<void>
}

function describe(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return 'That no longer applies: there is a pending action to decide first.'
    return `The server rejected the request (${err.status}).`
  }
  return 'Could not reach the server.'
}

export function useChat(): Chat {
  const [threadId, setThreadId] = useState<string | null>(null)
  const [events, setEvents] = useState<ApiEvent[]>([])
  const [state, setState] = useState<ThreadState | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [pendingText, setPendingText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  const load = useCallback(async (): Promise<string | null> => {
    const stored = loadThreadId()
    if (stored) {
      try {
        const turn = await getThread(stored)
        setThreadId(stored)
        setEvents(turn.events)
        setState(turn.state)
        return stored
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) throw err
        // The server forgot the thread: start another one (D18)
      }
    }
    const created = await createThread()
    saveThreadId(created.thread_id)
    setThreadId(created.thread_id)
    setEvents(created.events)
    setState(created.state)
    return created.thread_id
  }, [])

  useEffect(() => {
    if (started.current) return
    started.current = true
    load()
      .catch((err) => setError(describe(err)))
      .finally(() => setLoading(false))
  }, [load])

  const refresh = useCallback(async () => {
    if (!threadId) return
    try {
      const turn = await getThread(threadId)
      setEvents(turn.events)
      setState(turn.state)
    } catch {
      // keep what is on screen
    }
  }, [threadId])

  const run = useCallback(
    async (request: () => Promise<TurnResponse>): Promise<boolean> => {
      setBusy(true)
      setError(null)
      try {
        const turn = await request()
        setEvents((prev) => [...prev, ...turn.events])
        setState(turn.state)
        return true
      } catch (err) {
        setError(describe(err))
        if (threadId && err instanceof ApiError && err.status === 404) {
          await load().catch(() => undefined)
        } else {
          // 409 and friends: the server state is the truth
          await refresh()
        }
        return false
      } finally {
        setBusy(false)
      }
    },
    [threadId, load, refresh],
  )

  const send = useCallback(
    async (text: string) => {
      if (!threadId) return false
      setPendingText(text)
      try {
        return await run(() => sendMessage(threadId, text))
      } finally {
        setPendingText(null)
      }
    },
    [threadId, run],
  )

  const decideAction = useCallback(
    async (interruptId: string, decision: Decision) => {
      if (!threadId) return
      await run(() => decide(threadId, interruptId, decision))
    },
    [threadId, run],
  )

  return { loading, busy, events, state, pendingText, error, send, decide: decideAction, refresh }
}
