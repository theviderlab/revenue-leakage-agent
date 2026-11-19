// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { ApiEvent, ThreadState, TurnResponse } from './lib/types'
import {
  audit,
  emptyState,
  findingsEvent,
  PLAN,
  pendingAction,
  planFindings,
  proposal,
  SETTINGS,
} from './test/fixtures'

// A tiny in-memory server: the same flow as the mock (check, propose, apply, approve / reject).
class FakeServer {
  events: ApiEvent[] = []
  state: ThreadState = emptyState()
  threads = new Set<string>(['thr-1'])
  requests: { method: string; path: string; body: unknown }[] = []
  settingsStatus = 200

  handle = async (url: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? 'GET'
    const path = url.replace(/^\/api/, '')
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    this.requests.push({ method, path, body })
    const json = (data: unknown, status = 200) =>
      new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

    if (path === '/plans') return json([PLAN])
    if (path === '/settings' && method === 'GET') return json(SETTINGS)
    if (path === '/settings') {
      if (this.settingsStatus !== 200) return json({ detail: 'pending' }, this.settingsStatus)
      return json({ ...SETTINGS, version: 2 })
    }
    if (path === '/threads' && method === 'POST') {
      this.threads.add('thr-new')
      this.events = []
      this.state = emptyState()
      return json({ thread_id: 'thr-new', events: [], state: this.state })
    }
    const thread = /^\/threads\/([^/]+)(\/\w+)?$/.exec(path)
    if (!thread || !this.threads.has(thread[1])) return json({ detail: 'Thread not found' }, 404)
    if (!thread[2]) return json({ events: this.events, state: this.state })
    if (thread[2] === '/messages') {
      if (this.state.pending_approval) return json({ detail: 'pending' }, 409)
      return json(this.message(body.text))
    }
    return json(this.decision(body.interrupt_id, body.decision))
  }

  private respond(turn: ApiEvent[]): TurnResponse {
    this.events = [...this.events, ...turn]
    return { events: turn, state: this.state }
  }

  private message(text: string): TurnResponse {
    const user: ApiEvent = { type: 'user_message', text }
    if (/check/i.test(text)) {
      const f = planFindings(43500)
      this.state = { ...this.state, findings: [f] }
      return this.respond([
        user,
        { type: 'tool_call', tool_call_id: 'call_1', name: 'detect_anomalies', args: { plan_id: 'PLN-1001' } },
        { type: 'tool_result', tool_call_id: 'call_1', name: 'detect_anomalies', status: 'ok', result: {} },
        findingsEvent('call_1', f),
        { type: 'assistant_message', text: 'Plan **PLN-1001** is missing 5,500.' },
      ])
    }
    if (/propose/i.test(text)) {
      this.state = { ...this.state, proposals: [proposal('pending')] }
      return this.respond([
        user,
        { type: 'tool_call', tool_call_id: 'call_2', name: 'propose_make_good_invoice', args: {} },
        { type: 'tool_result', tool_call_id: 'call_2', name: 'propose_make_good_invoice', status: 'ok', result: {} },
        { type: 'proposal', tool_call_id: 'call_2', proposal: proposal('pending') },
        { type: 'assistant_message', text: 'Proposal PRP-001 is ready.' },
      ])
    }
    if (/apply/i.test(text)) {
      const action = pendingAction()
      this.state = { ...this.state, pending_approval: { interrupt_id: 'int-1', action } }
      return this.respond([
        user,
        { type: 'tool_call', tool_call_id: 'call_3', name: 'apply', args: { proposal_id: 'PRP-001' } },
        { type: 'approval_request', tool_call_id: 'call_3', interrupt_id: 'int-1', action },
      ])
    }
    return this.respond([user, { type: 'assistant_message', text: 'Name the plan.' }])
  }

  private decision(interruptId: string, decision: 'approve' | 'reject'): TurnResponse {
    if (this.state.pending_approval?.interrupt_id !== interruptId) {
      throw new Error('stale interrupt_id reached the server')
    }
    if (decision === 'reject') {
      this.state = { ...this.state, pending_approval: null, proposals: [proposal('rejected')] }
      return this.respond([
        { type: 'tool_result', tool_call_id: 'call_3', name: 'apply', status: 'rejected_by_user', result: {} },
        { type: 'assistant_message', text: 'Nothing was written.' },
      ])
    }
    const f = planFindings(50500)
    this.state = {
      ...this.state,
      pending_approval: null,
      proposals: [proposal('applied')],
      actions: [audit()],
      findings: [f],
    }
    return this.respond([
      {
        type: 'tool_result',
        tool_call_id: 'call_3',
        name: 'apply',
        status: 'ok',
        result: { status: 'applied', action_id: 'INV-MG-001' },
      },
      { type: 'tool_call', tool_call_id: 'call_4', name: 'detect_anomalies', args: {} },
      { type: 'tool_result', tool_call_id: 'call_4', name: 'detect_anomalies', status: 'ok', result: {} },
      findingsEvent('call_4', f),
      { type: 'assistant_message', text: 'Applied. Re-checked.' },
    ])
  }
}

let server: FakeServer

beforeEach(() => {
  localStorage.clear()
  server = new FakeServer()
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => server.handle(url, init)))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function say(text: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Message'), `${text}{Enter}`)
}

describe('chat flow against a fake server', () => {
  it('runs check, propose, approve and shows the confirmation from the tool_result', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText(/Name the plan you/)
    expect(localStorage.getItem('revenue-leakage-agent.thread_id')).toBe('thr-new')

    await say('check plan PLN-1001')
    const findings = await screen.findByTestId('findings-PLN-1001')
    expect(findings.textContent).toContain('-5,500.00')
    expect((screen.getByTestId('trace') as HTMLDetailsElement).open).toBe(false)

    await say('propose a make-good')
    expect((await screen.findByTestId('proposal-status')).textContent).toBe('pending')

    await say('apply it')
    const card = await screen.findByTestId('approval-card')
    expect(screen.getByLabelText('Message')).toHaveProperty('disabled', true)

    await user.click(within(card).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.getByTestId('approval-outcome').textContent).toContain('INV-MG-001'))
    expect(server.requests.find((r) => r.path.endsWith('/decisions'))?.body).toEqual({ interrupt_id: 'int-1', decision: 'approve' })
    // The proposal card follows the thread state, and the older findings are marked as replaced.
    expect(screen.getByTestId('proposal-status').textContent).toBe('applied')
    expect(screen.getByText('superseded by a later check')).toBeTruthy()
    expect(screen.getByLabelText('Message')).toHaveProperty('disabled', false)

    await user.click(screen.getByRole('tab', { name: 'Audit log' }))
    expect(screen.getByText('INV-MG-001', { selector: 'strong' })).toBeTruthy()
  })

  it('rejecting writes nothing and says so', async () => {
    const user = userEvent.setup()
    render(<App />)
    await say('check plan PLN-1001')
    await screen.findByTestId('findings-PLN-1001')
    await say('propose')
    await screen.findByTestId('proposal-card')
    await say('apply')
    await user.click(within(await screen.findByTestId('approval-card')).getByRole('button', { name: 'Reject' }))
    await waitFor(() =>
      expect(screen.getByTestId('approval-outcome').textContent).toContain('Nothing was written'),
    )
    expect(screen.getByTestId('proposal-status').textContent).toBe('rejected')
  })

  it('restores the history after a reload', async () => {
    render(<App />)
    await say('check plan PLN-1001')
    await screen.findByTestId('findings-PLN-1001')
    cleanup()

    render(<App />)
    expect((await screen.findByTestId('findings-PLN-1001')).textContent).toContain('-5,500.00')
    expect(screen.getByText('check plan PLN-1001')).toBeTruthy()
    expect(server.requests.filter((r) => r.method === 'POST' && r.path === '/threads')).toHaveLength(1)
  })

  it('shows a pending approval again after a reload', async () => {
    render(<App />)
    await say('check plan PLN-1001')
    await screen.findByTestId('findings-PLN-1001')
    await say('propose')
    await screen.findByTestId('proposal-card')
    await say('apply')
    await screen.findByTestId('approval-card')
    cleanup()

    render(<App />)
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeTruthy()
  })

  it('creates another thread when the stored one is gone (404)', async () => {
    localStorage.setItem('revenue-leakage-agent.thread_id', 'thr-gone')
    render(<App />)
    await screen.findByText(/Name the plan you/)
    expect(localStorage.getItem('revenue-leakage-agent.thread_id')).toBe('thr-new')
  })

  it('requests the plan list once per load', async () => {
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
    await screen.findByText(/Name the plan you/)
    await screen.findByRole('button', { name: /PLN-1001/ })
    expect(server.requests.filter((r) => r.path === '/plans')).toHaveLength(1)
  })

  it('writes a plan shortcut in the input without sending it', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText(/Name the plan you/)
    await user.click(await screen.findByRole('button', { name: /PLN-1001/ }))
    expect(screen.getByLabelText('Message')).toHaveProperty(
      'value',
      'Check plan PLN-1001 for revenue leakage',
    )
    expect(server.requests.some((r) => r.path.endsWith('/messages'))).toBe(false)
  })
})

describe('settings panel', () => {
  it('saves only the changed setting', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('tab', { name: 'Settings' }))
    await user.selectOptions(await screen.findByLabelText('Period calendar'), 'calendar')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() =>
      expect(server.requests.find((r) => r.method === 'PUT')?.body).toEqual({
        period_calendar: 'calendar',
      }),
    )
  })

  it('shows a 409 as a pending action', async () => {
    const user = userEvent.setup()
    server.settingsStatus = 409
    render(<App />)
    await user.click(screen.getByRole('tab', { name: 'Settings' }))
    await user.selectOptions(await screen.findByLabelText('Auto renewal'), 'true')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    expect((await screen.findByRole('alert')).textContent).toContain('pending action')
  })
})


