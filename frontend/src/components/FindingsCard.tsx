import { label, money, signedMoney } from '../lib/format'
import type { Block } from '../lib/turns'
import type { Period, PlanFindings } from '../lib/types'
import { Badge, type Tone } from './Badge'

type FindingsBlock = Extract<Block, { kind: 'findings' }>

const PERIOD_TONE: Record<Period['status'], Tone> = {
  ok: 'good',
  missing: 'bad',
  underbilled: 'bad',
  overbilled: 'warn',
  pending_decision: 'info',
  no_data: 'neutral',
}

function PlanTable({
  plan,
  currency,
  superseded,
  current,
}: {
  plan: PlanFindings
  currency?: string
  superseded: boolean
  current?: PlanFindings
}) {
  // Status now: the thread state knows whether the settings changed since this was calculated.
  const stale = current?.stale ?? plan.stale
  return (
    <section className={superseded ? 'opacity-60' : ''} data-testid={`findings-${plan.plan_id}`}>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <h4 className="font-semibold">{plan.plan_id}</h4>
        <Badge tone={plan.confidence === 'high' ? 'good' : 'warn'}>
          {plan.confidence} confidence
        </Badge>
        {stale && <Badge tone="warn">stale: settings changed</Badge>}
        {superseded && <Badge>superseded by a later check</Badge>}
        <span className="ml-auto text-xs text-slate-600">
          expected {money(plan.totals.expected, currency)} / billed{' '}
          {money(plan.totals.net, currency)} /{' '}
          <strong className={plan.totals.diff < 0 ? 'text-red-700' : ''}>
            {signedMoney(plan.totals.diff, currency)}
          </strong>
        </span>
      </div>
      {plan.confidence_reasons.length > 0 && (
        <p className="px-3 pb-2 text-xs text-amber-800">
          Low confidence because these settings are not fixed:{' '}
          {plan.confidence_reasons.map(label).join(', ')}. No proposals are allowed meanwhile.
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="px-3 py-1 font-medium">Period</th>
              <th className="px-3 py-1 font-medium">Status</th>
              <th className="px-3 py-1 text-right font-medium">Expected</th>
              <th className="px-3 py-1 text-right font-medium">Net</th>
              <th className="px-3 py-1 text-right font-medium">Diff</th>
              <th className="px-3 py-1 font-medium">Records</th>
            </tr>
          </thead>
          <tbody>
            {plan.periods.map((p) => (
              <tr key={p.period_start} className="border-t border-slate-100">
                <td className="whitespace-nowrap px-3 py-1">
                  {p.period_start}
                  <span className="text-slate-400"> to {p.period_end}</span>
                </td>
                <td className="px-3 py-1">
                  <Badge tone={PERIOD_TONE[p.status]}>{label(p.status)}</Badge>
                </td>
                <td className="px-3 py-1 text-right tabular-nums">{money(p.expected)}</td>
                <td className="px-3 py-1 text-right tabular-nums">{money(p.net)}</td>
                <td className="px-3 py-1 text-right tabular-nums">{signedMoney(p.diff)}</td>
                <td className="px-3 py-1">
                  {[...p.invoice_ids, ...p.memo_ids].join(', ')}
                  {p.flags.map((f) => (
                    <span key={f} className="ml-1">
                      <Badge>{label(f)}</Badge>
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function FindingsCard({
  block,
  current,
  currencyOf,
}: {
  block: FindingsBlock
  current: PlanFindings[]
  currencyOf: (planId: string) => string | undefined
}) {
  const { findings } = block
  const currencies = new Set(findings.plans.map((p) => currencyOf(p.plan_id)))
  const totalCurrency = currencies.size === 1 ? [...currencies][0] : undefined
  return (
    <div
      className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white text-sm"
      data-testid="findings-card"
    >
      <header className="flex flex-wrap items-center gap-2 bg-slate-50 px-3 py-2">
        <h3 className="font-semibold">Findings</h3>
        <span className="ml-auto text-xs text-slate-600">
          Total: expected {money(findings.totals.expected, totalCurrency)} / billed{' '}
          {money(findings.totals.net, totalCurrency)} /{' '}
          <strong className={findings.totals.diff < 0 ? 'text-red-700' : ''}>
            {signedMoney(findings.totals.diff, totalCurrency)}
          </strong>
        </span>
      </header>
      {findings.plans.map((plan) => (
        <PlanTable
          key={plan.plan_id}
          plan={plan}
          currency={currencyOf(plan.plan_id)}
          superseded={block.supersededPlanIds.includes(plan.plan_id)}
          current={current.find((c) => c.plan_id === plan.plan_id)}
        />
      ))}
      {findings.unexplained.length > 0 && (
        <section className="px-3 py-2 text-xs">
          <h4 className="mb-1 font-semibold">Unexplained records</h4>
          <ul className="space-y-0.5">
            {findings.unexplained.map((u) => (
              <li key={u.record_id}>
                <code>{u.record_id}</code> ({u.kind}, {money(u.amount, u.currency)}):{' '}
                {label(u.reason)}
                {u.candidates.length > 0 && <> / candidates: {u.candidates.join(', ')}</>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {findings.assumptions.length > 0 && (
        <details className="px-3 py-2 text-xs">
          <summary className="cursor-pointer text-slate-600">Assumptions</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {findings.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
