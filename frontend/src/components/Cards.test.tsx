// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { findingsEvent, pendingAction, planFindings, proposal } from '../test/fixtures'
import { buildTurns, type Block } from '../lib/turns'
import { ApprovalCard } from './ApprovalCard'
import { FindingsCard } from './FindingsCard'
import { ProposalCard } from './ProposalCard'
import { Trace } from './Trace'

afterEach(cleanup)

type Approval = Extract<Block, { kind: 'approval' }>
type Findings = Extract<Block, { kind: 'findings' }>

const approval = (outcome: Approval['outcome']): Approval => ({
  kind: 'approval',
  toolCallId: 'c3',
  interruptId: 'int-1',
  action: pendingAction(),
  outcome,
})

function findingsBlock(superseded: string[] = []): Findings {
  const [turn] = buildTurns([findingsEvent('c1', planFindings(43500))])
  const block = turn.blocks[0] as Findings
  return { ...block, supersededPlanIds: superseded }
}

describe('FindingsCard', () => {
  it('shows the totals computed by the API, with the plan currency', () => {
    render(<FindingsCard block={findingsBlock()} current={[]} currencyOf={() => 'USD'} />)
    const card = screen.getByTestId('findings-PLN-1001')
    expect(card.textContent).toContain('expected 49,000.00 USD')
    expect(card.textContent).toContain('billed 43,500.00 USD')
    expect(card.textContent).toContain('-5,500.00 USD')
    expect(card.textContent).toContain('Missing')
  })

  it('shows the current status: stale and superseded', () => {
    render(
      <FindingsCard
        block={findingsBlock(['PLN-1001'])}
        current={[planFindings(43500, { stale: true })]}
        currencyOf={() => undefined}
      />,
    )
    expect(screen.getByText('stale: settings changed')).toBeTruthy()
    expect(screen.getByText('superseded by a later check')).toBeTruthy()
  })

  it('explains a low confidence and that proposals are blocked', () => {
    const [turn] = buildTurns([
      findingsEvent(
        'c1',
        planFindings(43500, { confidence: 'low', confidence_reasons: ['period_calendar'] }),
      ),
    ])
    render(
      <FindingsCard block={turn.blocks[0] as Findings} current={[]} currencyOf={() => 'USD'} />,
    )
    expect(screen.getByText(/Period calendar/)).toBeTruthy()
  })
})

describe('ProposalCard', () => {
  it('shows the proposal and its status', () => {
    render(<ProposalCard proposal={proposal('applied')} />)
    expect(screen.getByText('PRP-001')).toBeTruthy()
    expect(screen.getByTestId('proposal-status').textContent).toBe('applied')
    expect(screen.getByText('Recover the missing February 2026 billing')).toBeTruthy()
    expect(screen.getByText('2026-02-01')).toBeTruthy()
  })
})

describe('ApprovalCard', () => {
  it('offers Approve and Reject while it is the pending approval', async () => {
    const onDecide = vi.fn()
    render(<ApprovalCard block={approval(null)} active busy={false} onDecide={onDecide} />)
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))
    expect(onDecide.mock.calls).toEqual([['approve'], ['reject']])
  })

  it('disables the buttons while a request is in flight', () => {
    render(<ApprovalCard block={approval(null)} active busy onDecide={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Approve' })).toHaveProperty('disabled', true)
  })

  it('confirms from the tool_result, quoting the real action id', () => {
    const outcome = { status: 'ok' as const, value: { status: 'applied', action_id: 'INV-MG-007' } }
    render(<ApprovalCard block={approval(outcome)} active={false} busy={false} onDecide={vi.fn()} />)
    expect(screen.getByTestId('approval-outcome').textContent).toContain('INV-MG-007')
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
  })

  it('says nothing was written after a rejection', () => {
    const outcome = { status: 'rejected_by_user' as const, value: {} }
    render(<ApprovalCard block={approval(outcome)} active={false} busy={false} onDecide={vi.fn()} />)
    expect(screen.getByTestId('approval-outcome').textContent).toContain('Nothing was written')
  })

  it('shows the domain error of a failed action', () => {
    const outcome = { status: 'error' as const, value: { status: 'error', error: 'Not the last' } }
    render(<ApprovalCard block={approval(outcome)} active={false} busy={false} onDecide={vi.fn()} />)
    expect(screen.getByTestId('approval-outcome').textContent).toContain('Not the last')
  })

  it('has no buttons for an old card that is not the pending one', () => {
    render(<ApprovalCard block={approval(null)} active={false} busy={false} onDecide={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
  })
})

describe('Trace', () => {
  it('is collapsed by default and lists the tool calls', () => {
    render(
      <Trace
        steps={[
          { toolCallId: 'c1', name: 'detect_anomalies', args: { plan_id: 'PLN-1001' }, result: null },
        ]}
      />,
    )
    const details = screen.getByTestId('trace') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('1 tool call')
    expect(details.textContent).toContain('detect_anomalies')
    expect(details.textContent).toContain('waiting for approval')
  })
})



