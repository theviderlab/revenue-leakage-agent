import { isRecord } from '../lib/format'
import type { Block } from '../lib/turns'
import type { Decision } from '../lib/types'
import { Badge } from './Badge'
import { ProposalArgs } from './ProposalCard'

type ApprovalBlock = Extract<Block, { kind: 'approval' }>

// The confirmation comes from the tool_result (D28), never from the assistant text.
function Outcome({ outcome }: { outcome: NonNullable<ApprovalBlock['outcome']> }) {
  const value = isRecord(outcome.value) ? outcome.value : {}
  if (outcome.status === 'ok') {
    const id = typeof value.action_id === 'string' ? value.action_id : null
    return (
      <p data-testid="approval-outcome">
        <Badge tone="good">Approved</Badge> {value.status === 'rolled_back' ? 'Rolled back' : 'Written'}
        {id && (
          <>
            : <code>{id}</code>
          </>
        )}
      </p>
    )
  }
  if (outcome.status === 'rejected_by_user') {
    return (
      <p data-testid="approval-outcome">
        <Badge tone="warn">Rejected</Badge> Nothing was written.
      </p>
    )
  }
  const reason = typeof value.error === 'string' ? value.error : 'The action failed.'
  return (
    <p data-testid="approval-outcome">
      <Badge tone="bad">Failed</Badge> {reason}
    </p>
  )
}

export function ApprovalCard({
  block,
  active,
  busy,
  onDecide,
}: {
  block: ApprovalBlock
  // True when this is the thread's pending approval (state.pending_approval)
  active: boolean
  busy: boolean
  onDecide: (decision: Decision) => void
}) {
  const { action } = block
  const awaiting = active && !block.outcome
  return (
    <div
      className={`space-y-2 rounded-lg border-2 bg-white p-3 text-sm ${
        awaiting ? 'border-indigo-400' : 'border-slate-200'
      }`}
      data-testid="approval-card"
    >
      <div className="flex items-center gap-2">
        <h3 className="font-semibold">
          {action.kind === 'apply' ? 'Approval needed' : 'Rollback approval needed'}
        </h3>
        {action.action_id && <code className="text-xs">{action.action_id}</code>}
      </div>
      <p>{action.summary}</p>
      {action.proposal && <ProposalArgs args={action.proposal.args} />}
      <details className="text-xs" open={awaiting}>
        <summary className="cursor-pointer text-slate-600">Evidence</summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-4">
          {action.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      </details>
      {block.outcome ? (
        <Outcome outcome={block.outcome} />
      ) : awaiting ? (
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide('approve')}
            className="rounded-md bg-emerald-600 px-3 py-1.5 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide('reject')}
            className="rounded-md border border-slate-300 px-3 py-1.5 font-medium hover:bg-slate-50 disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      ) : (
        <p className="text-slate-500" data-testid="approval-outcome">
          This request is no longer pending.
        </p>
      )}
    </div>
  )
}
