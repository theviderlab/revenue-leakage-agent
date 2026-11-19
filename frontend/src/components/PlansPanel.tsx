import { money } from '../lib/format'
import type { Plan } from '../lib/types'
import { Badge } from './Badge'

export function PlansPanel({
  plans,
  error,
  onPick,
}: {
  plans: Plan[] | null
  error: string | null
  onPick: (plan: Plan) => void
}) {
  if (error) return <p className="text-sm text-red-700">{error}</p>
  if (!plans) return <p className="text-sm text-slate-500">Loading plans...</p>
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-500">Click a plan to write a request in the chat.</p>
      <ul className="space-y-2">
        {plans.map((p) => (
          <li key={p.plan_id}>
            <button
              type="button"
              onClick={() => onPick(p)}
              className="w-full rounded-lg border border-slate-200 bg-white p-2 text-left text-sm hover:border-indigo-300 hover:bg-indigo-50"
            >
              <span className="flex items-center gap-2">
                <strong>{p.plan_id}</strong>
                {p.source === 'sandbox' && <Badge tone="info">sandbox</Badge>}
                <span className="ml-auto text-xs text-slate-500">{p.cadence}</span>
              </span>
              <span className="block text-slate-700">{p.customer_name}</span>
              <span className="block text-xs text-slate-500">
                {money(p.total_value, p.currency)} from {p.start_date}
                {p.amends && <> / amends {p.amends}</>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
