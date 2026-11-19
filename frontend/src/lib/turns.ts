// Reducer from the flat API event list to chat turns. It is a pure function of the whole list, so
// a turn answered live and a thread reloaded with GET /threads/{id} render the same.
import type { ApiEvent, EventOf, FindingsPayload, PendingAction, Proposal, ToolStatus } from './types'

export interface TraceStep {
  toolCallId: string
  name: string
  args: Record<string, unknown>
  // null while the tool has not answered (an action waiting for approval)
  result: { status: ToolStatus; value: unknown } | null
}

export type Block =
  | { kind: 'assistant'; text: string }
  | {
      kind: 'findings'
      toolCallId: string
      findings: FindingsPayload
      // Plans that a later findings event recalculated
      supersededPlanIds: string[]
    }
  | { kind: 'proposal'; toolCallId: string; proposal: Proposal }
  | {
      kind: 'approval'
      toolCallId: string
      interruptId: string
      action: PendingAction
      // The tool_result of the same tool_call_id, once the user decided
      outcome: TraceStep['result']
    }
  | { kind: 'error'; message: string }

export interface Turn {
  id: string
  userText: string | null
  trace: TraceStep[]
  blocks: Block[]
}

export function buildTurns(events: ApiEvent[]): Turn[] {
  const turns: Turn[] = []
  let steps = new Map<string, TraceStep>()
  let current: Turn | null = null

  const turn = (): Turn => {
    if (!current) {
      current = { id: `turn-${turns.length}`, userText: null, trace: [], blocks: [] }
      turns.push(current)
    }
    return current
  }

  const step = (e: EventOf<'tool_call'> | EventOf<'tool_result'>): TraceStep => {
    let s = steps.get(e.tool_call_id)
    if (!s) {
      s = { toolCallId: e.tool_call_id, name: e.name, args: {}, result: null }
      steps.set(e.tool_call_id, s)
      turn().trace.push(s)
    }
    return s
  }

  for (const e of events) {
    switch (e.type) {
      case 'user_message':
        current = { id: `turn-${turns.length}`, userText: e.text, trace: [], blocks: [] }
        turns.push(current)
        steps = new Map()
        break
      case 'assistant_message':
        turn().blocks.push({ kind: 'assistant', text: e.text })
        break
      case 'tool_call':
        step(e).args = e.args
        break
      case 'tool_result':
        step(e).result = { status: e.status, value: e.result }
        break
      case 'findings':
        turn().blocks.push({
          kind: 'findings',
          toolCallId: e.tool_call_id,
          findings: e.findings,
          supersededPlanIds: [],
        })
        break
      case 'proposal':
        turn().blocks.push({ kind: 'proposal', toolCallId: e.tool_call_id, proposal: e.proposal })
        break
      case 'approval_request':
        turn().blocks.push({
          kind: 'approval',
          toolCallId: e.tool_call_id,
          interruptId: e.interrupt_id,
          action: e.action,
          outcome: null,
        })
        break
      case 'error':
        turn().blocks.push({ kind: 'error', message: e.message })
        break
    }
  }

  // The decision answers in a later response than the approval card: pair them by tool_call_id.
  for (const t of turns) {
    for (const b of t.blocks) {
      if (b.kind === 'approval') {
        b.outcome = t.trace.find((s) => s.toolCallId === b.toolCallId)?.result ?? null
      }
    }
  }

  // A recalculation (D23) replaces earlier findings of the same plan.
  const latest = new Set<string>()
  for (const t of [...turns].reverse()) {
    for (const b of [...t.blocks].reverse()) {
      if (b.kind !== 'findings') continue
      b.supersededPlanIds = b.findings.plans.map((p) => p.plan_id).filter((id) => latest.has(id))
      b.findings.plans.forEach((p) => latest.add(p.plan_id))
    }
  }
  return turns
}
