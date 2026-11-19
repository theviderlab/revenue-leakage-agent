import { describe, expect, it } from 'vitest'
import { findingsEvent, pendingAction, planFindings, proposal } from '../test/fixtures'
import { buildTurns } from './turns'
import type { ApiEvent } from './types'

const user = (text: string): ApiEvent => ({ type: 'user_message', text })
const say = (text: string): ApiEvent => ({ type: 'assistant_message', text })
const call = (id: string, name: string, args: Record<string, unknown> = {}): ApiEvent => ({
  type: 'tool_call',
  tool_call_id: id,
  name,
  args,
})
const result = (
  id: string,
  name: string,
  status: 'ok' | 'error' | 'rejected_by_user',
  r: unknown = {},
): ApiEvent => ({ type: 'tool_result', tool_call_id: id, name, status, result: r })

describe('buildTurns', () => {
  it('splits turns at each user message and pairs calls with results', () => {
    const turns = buildTurns([
      user('check'),
      call('c1', 'detect_anomalies', { plan_id: 'PLN-1001' }),
      result('c1', 'detect_anomalies', 'ok'),
      say('done'),
      user('again'),
      say('ok'),
    ])
    expect(turns).toHaveLength(2)
    expect(turns[0].userText).toBe('check')
    expect(turns[0].trace).toEqual([
      {
        toolCallId: 'c1',
        name: 'detect_anomalies',
        args: { plan_id: 'PLN-1001' },
        result: { status: 'ok', value: {} },
      },
    ])
    expect(turns[0].blocks).toEqual([{ kind: 'assistant', text: 'done' }])
    expect(turns[1].trace).toEqual([])
  })

  it('keeps findings and proposals in the turn of their tool call', () => {
    const turns = buildTurns([
      user('check'),
      call('c1', 'detect_anomalies'),
      result('c1', 'detect_anomalies', 'ok'),
      findingsEvent('c1', planFindings(43500)),
      user('propose'),
      call('c2', 'propose_make_good_invoice'),
      result('c2', 'propose_make_good_invoice', 'ok'),
      { type: 'proposal', tool_call_id: 'c2', proposal: proposal() },
    ])
    expect(turns[0].blocks.map((b) => b.kind)).toEqual(['findings'])
    expect(turns[1].blocks.map((b) => b.kind)).toEqual(['proposal'])
  })

  it('folds the decision response into the turn that asked for approval', () => {
    const asked: ApiEvent[] = [
      user('apply it'),
      call('c3', 'apply', { proposal_id: 'PRP-001' }),
      {
        type: 'approval_request',
        tool_call_id: 'c3',
        interrupt_id: 'int-1',
        action: pendingAction(),
      },
    ]
    const before = buildTurns(asked)
    expect(before[0].blocks[0]).toMatchObject({ kind: 'approval', outcome: null })
    expect(before[0].trace[0].result).toBeNull()

    const after = buildTurns([
      ...asked,
      result('c3', 'apply', 'ok', { status: 'applied', action_id: 'INV-MG-001' }),
      say('Applied'),
    ])
    expect(after).toHaveLength(1)
    expect(after[0].blocks[0]).toMatchObject({
      kind: 'approval',
      outcome: { status: 'ok', value: { action_id: 'INV-MG-001' } },
    })
    expect(after[0].blocks.map((b) => b.kind)).toEqual(['approval', 'assistant'])
  })

  it('marks earlier findings of a plan as superseded by a recalculation', () => {
    const turns = buildTurns([
      user('check'),
      findingsEvent('c1', planFindings(43500)),
      user('apply'),
      findingsEvent('c9', planFindings(50500)),
    ])
    const [first, second] = turns.map((t) => t.blocks[0])
    expect(first).toMatchObject({ kind: 'findings', supersededPlanIds: ['PLN-1001'] })
    expect(second).toMatchObject({ kind: 'findings', supersededPlanIds: [] })
  })

  it('does not mix tool call ids across turns', () => {
    const turns = buildTurns([
      user('a'),
      call('call_1', 'x'),
      result('call_1', 'x', 'ok'),
      user('b'),
      call('call_1', 'y'),
    ])
    expect(turns[1].trace).toHaveLength(1)
    expect(turns[1].trace[0]).toMatchObject({ name: 'y', result: null })
  })
})


