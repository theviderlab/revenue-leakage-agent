import { api } from './api'
import type {
  CreateThreadResponse,
  Decision,
  Plan,
  SettingsResponse,
  SettingsUpdate,
  TurnResponse,
} from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const createThread = () => post<CreateThreadResponse>('/threads')
export const getThread = (id: string) => api<TurnResponse>(`/threads/${id}`)
export const sendMessage = (id: string, text: string) =>
  post<TurnResponse>(`/threads/${id}/messages`, { text })
export const decide = (id: string, interruptId: string, decision: Decision) =>
  post<TurnResponse>(`/threads/${id}/decisions`, { interrupt_id: interruptId, decision })
// Concurrent callers share one request (StrictMode runs effects twice in dev).
let plansInFlight: Promise<Plan[]> | null = null
export function listPlans(): Promise<Plan[]> {
  plansInFlight ??= api<Plan[]>('/plans').finally(() => {
    plansInFlight = null
  })
  return plansInFlight
}
export const getSettings = () => api<SettingsResponse>('/settings')
export const putSettings = (update: SettingsUpdate) =>
  api<SettingsResponse>('/settings', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(update),
  })
