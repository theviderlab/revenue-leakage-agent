import { useCallback, useEffect, useState } from 'react'
import { AuditLog } from './components/AuditLog'
import { Chat } from './components/Chat'
import { PlansPanel } from './components/PlansPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { listPlans } from './lib/client'
import { useChat } from './lib/useChat'
import type { Plan } from './lib/types'

type Tab = 'plans' | 'settings' | 'audit'

const TABS: { id: Tab; text: string }[] = [
  { id: 'plans', text: 'Plans' },
  { id: 'settings', text: 'Settings' },
  { id: 'audit', text: 'Audit log' },
]

function App() {
  const chat = useChat()
  const [tab, setTab] = useState<Tab>('plans')
  const [plans, setPlans] = useState<Plan[] | null>(null)
  const [plansError, setPlansError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [focusSignal, setFocusSignal] = useState(0)
  const { state } = chat

  // Applied actions are part of the plans (overlay, D6): reload the list when the audit log changes.
  // Empty until the thread loads, so loading it does not count as a change.
  const auditKey = (state?.actions ?? []).map((a) => `${a.action_id}:${a.rolled_back_at ?? ''}`).join()
  useEffect(() => {
    listPlans()
      .then((p) => {
        setPlans(p)
        setPlansError(null)
      })
      .catch(() => setPlansError('Could not load the plans.'))
  }, [auditKey])

  const pickPlan = useCallback((plan: Plan) => {
    setDraft(`Check plan ${plan.plan_id} for revenue leakage`)
    setFocusSignal((n) => n + 1)
  }, [])

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-4 py-2">
        <h1 className="text-lg font-semibold">Revenue Leakage Agent</h1>
      </header>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <Chat
            chat={chat}
            plans={plans ?? []}
            draft={draft}
            onDraftChange={setDraft}
            focusSignal={focusSignal}
          />
        </main>
        <aside className="flex max-h-[45vh] min-h-0 shrink-0 flex-col border-t border-slate-200 bg-white md:max-h-none md:w-96 md:border-l md:border-t-0">
          <div role="tablist" className="flex border-b border-slate-200">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                type="button"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`flex-1 px-3 py-2 text-sm font-medium ${
                  tab === t.id
                    ? 'border-b-2 border-indigo-600 text-indigo-700'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                {t.text}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {tab === 'plans' && <PlansPanel plans={plans} error={plansError} onPick={pickPlan} />}
            {tab === 'settings' && <SettingsPanel onSaved={chat.refresh} />}
            {tab === 'audit' && <AuditLog actions={state?.actions ?? []} />}
          </div>
        </aside>
      </div>
    </div>
  )
}

export default App
