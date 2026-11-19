import { dateTime, label, pretty } from '../lib/format'
import type { AuditAction } from '../lib/types'
import { Badge } from './Badge'

export function AuditLog({ actions }: { actions: AuditAction[] }) {
  if (actions.length === 0) {
    return <p className="text-sm text-slate-500">No actions have been applied yet.</p>
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-500">
        Global sandbox history, across all conversations. Newest first.
      </p>
      <ul className="space-y-2">
        {[...actions].reverse().map((a) => (
          <li key={a.action_id}>
            <details className="rounded-lg border border-slate-200 bg-white text-sm">
              <summary className="cursor-pointer space-y-0.5 p-2">
                <span className="flex items-center gap-2">
                  <strong>{a.action_id}</strong>
                  {a.rolled_back_at ? (
                    <Badge tone="warn">rolled back</Badge>
                  ) : (
                    <Badge tone="good">active</Badge>
                  )}
                </span>
                <span className="block text-xs text-slate-600">
                  {label(a.type)}
                  {a.plan_id && <> / {a.plan_id}</>}
                </span>
                <span className="block text-xs text-slate-500">
                  Applied {dateTime(a.applied_at)}
                  {a.rolled_back_at && <> / rolled back {dateTime(a.rolled_back_at)}</>}
                </span>
              </summary>
              <div className="space-y-2 border-t border-slate-100 p-2 text-xs">
                <p>{a.reason}</p>
                <p className="text-slate-500">
                  Proposal {a.proposal_id} in thread {a.thread_id}
                </p>
                <ul className="list-disc space-y-0.5 pl-4">
                  {a.evidence.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
                <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-2">
                  {pretty(a.payload)}
                </pre>
              </div>
            </details>
          </li>
        ))}
      </ul>
    </div>
  )
}
