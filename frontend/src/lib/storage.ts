const THREAD_KEY = 'revenue-leakage-agent.thread_id'

// localStorage can throw (private windows, blocked site data): the app works without it.
export function loadThreadId(): string | null {
  try {
    return localStorage.getItem(THREAD_KEY)
  } catch {
    return null
  }
}

export function saveThreadId(id: string): void {
  try {
    localStorage.setItem(THREAD_KEY, id)
  } catch {
    // ignore: the thread is just not remembered
  }
}
