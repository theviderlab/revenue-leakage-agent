import { formatValue, label, money } from '../lib/format'
import type { Proposal } from '../lib/types'
import { Badge, type Tone } from './Badge'

const STATUS: Record<Proposal['status'], Tone> = {
  pending: 'info',
  applied: 'good',
  rejected: 'warn',
  invalid: 'bad',
}

export function ProposalArgs({ args }: { args: Record<string, unknown> }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {Object.entries(args).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{label(k)}</dt>
          <dd>{k === 'amount' && typeof v === 'number' ? money(v) : formatValue(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

export function ProposalCard({ proposal }: { proposal: Proposal }) {
  return (
    <div
      className="space-y-2 rounded-lg border border-slate-200 bg-white p-3 text-sm"
      data-testid="proposal-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{proposal.proposal_id}</h3>
        <span className="text-slate-600">{label(proposal.type)}</span>
        {proposal.plan_id && <code className="text-xs">{proposal.plan_id}</code>}
        <span className="ml-auto" data-testid="proposal-status">
          <Badge tone={STATUS[proposal.status]}>{proposal.status}</Badge>
        </span>
      </div>
      <p>{proposal.reason}</p>
      <ProposalArgs args={proposal.args} />
      <details className="text-xs">
        <summary className="cursor-pointer text-slate-600">Evidence</summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-4">
          {proposal.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}
