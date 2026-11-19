import { pretty } from '../lib/format'
import type { TraceStep } from '../lib/turns'
import { Badge, type Tone } from './Badge'

const STATUS: Record<string, { tone: Tone; text: string }> = {
  ok: { tone: 'good', text: 'ok' },
  error: { tone: 'bad', text: 'error' },
  rejected_by_user: { tone: 'warn', text: 'rejected by user' },
}

export function Trace({ steps }: { steps: TraceStep[] }) {
  if (steps.length === 0) return null
  return (
    <details className="rounded-lg border border-slate-200 bg-white text-sm" data-testid="trace">
      <summary className="cursor-pointer select-none px-3 py-1.5 text-slate-600">
        Trace: {steps.length} tool {steps.length === 1 ? 'call' : 'calls'}
      </summary>
      <ol className="divide-y divide-slate-100 border-t border-slate-100">
        {steps.map((s) => {
          const status = s.result ? STATUS[s.result.status] : null
          return (
            <li key={s.toolCallId} className="space-y-1 px-3 py-2">
              <div className="flex items-center gap-2">
                <code className="font-semibold">{s.name}</code>
                {status ? (
                  <Badge tone={status.tone}>{status.text}</Badge>
                ) : (
                  <Badge tone="info">waiting for approval</Badge>
                )}
              </div>
              <pre className="overflow-x-auto rounded bg-slate-50 p-2 text-xs">
                {pretty(s.args)}
              </pre>
              {s.result && (
                <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-2 text-xs">
                  {pretty(s.result.value)}
                </pre>
              )}
            </li>
          )
        })}
      </ol>
    </details>
  )
}
