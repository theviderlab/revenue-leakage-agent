import { useEffect, useMemo, useRef, type FormEvent, type KeyboardEvent } from 'react'
import { buildTurns, type Block, type Turn } from '../lib/turns'
import type { Chat as ChatModel } from '../lib/useChat'
import type { Plan, ThreadState } from '../lib/types'
import { ApprovalCard } from './ApprovalCard'
import { FindingsCard } from './FindingsCard'
import { Markdown } from './Markdown'
import { ProposalCard } from './ProposalCard'
import { Trace } from './Trace'

interface Props {
  chat: ChatModel
  plans: Plan[]
  draft: string
  onDraftChange: (text: string) => void
  // Changes every time something outside asks to focus the input (a plan shortcut)
  focusSignal: number
}

function BlockView({
  block,
  state,
  plans,
  busy,
  onDecide,
}: {
  block: Block
  state: ThreadState
  plans: Plan[]
  busy: boolean
  onDecide: ChatModel['decide']
}) {
  switch (block.kind) {
    case 'assistant':
      return (
        <div className="rounded-lg bg-white px-3 py-2 shadow-sm ring-1 ring-slate-200">
          <Markdown>{block.text}</Markdown>
        </div>
      )
    case 'findings':
      return (
        <FindingsCard
          block={block}
          current={state.findings}
          currencyOf={(id) => plans.find((p) => p.plan_id === id)?.currency}
        />
      )
    case 'proposal': {
      // Status now: the thread state tracks the proposal after it was created (D34).
      const current = state.proposals.find((p) => p.proposal_id === block.proposal.proposal_id)
      return <ProposalCard proposal={current ?? block.proposal} />
    }
    case 'approval':
      return (
        <ApprovalCard
          block={block}
          active={state.pending_approval?.interrupt_id === block.interruptId}
          busy={busy}
          onDecide={(decision) => onDecide(block.interruptId, decision)}
        />
      )
    case 'error':
      return (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          Agent error: {block.message}
        </div>
      )
  }
}

function TurnView({
  turn,
  state,
  plans,
  busy,
  onDecide,
}: {
  turn: Turn
  state: ThreadState
  plans: Plan[]
  busy: boolean
  onDecide: ChatModel['decide']
}) {
  return (
    <article className="space-y-2" data-testid="turn">
      {turn.userText !== null && <UserBubble text={turn.userText} />}
      <Trace steps={turn.trace} />
      {turn.blocks.map((block, i) => (
        <BlockView key={i} block={block} state={state} plans={plans} busy={busy} onDecide={onDecide} />
      ))}
    </article>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white">
        {text}
      </p>
    </div>
  )
}

export function Chat({ chat, plans, draft, onDraftChange, focusSignal }: Props) {
  const { events, state, busy, loading, pendingText, error } = chat
  const turns = useMemo(() => buildTurns(events), [events])
  const input = useRef<HTMLTextAreaElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const awaitingDecision = state?.pending_approval != null

  useEffect(() => {
    if (focusSignal > 0) input.current?.focus()
  }, [focusSignal])

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'end' })
  }, [events, pendingText])

  async function submit(e?: FormEvent) {
    e?.preventDefault()
    const text = draft.trim()
    if (!text || busy || awaitingDecision) return
    onDraftChange('')
    if (!(await chat.send(text))) onDraftChange(text)
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4" data-testid="messages">
        {loading && <p className="text-sm text-slate-500">Loading conversation...</p>}
        {!loading && state && turns.length === 0 && !pendingText && (
          <div className="mx-auto max-w-md rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-600">
            <p className="font-medium text-slate-800">Revenue leakage agent</p>
            <p className="mt-1">
              I compare what each billing plan expects with what was invoiced. Name the plan you
              want me to look at, for example{' '}
              <em>&quot;Check plan PLN-1001 for revenue leakage&quot;</em>. You can also pick one
              from the Plans tab.
            </p>
          </div>
        )}
        {state &&
          turns.map((turn) => (
            <TurnView
              key={turn.id}
              turn={turn}
              state={state}
              plans={plans}
              busy={busy}
              onDecide={chat.decide}
            />
          ))}
        {pendingText && (
          <div className="space-y-2">
            <UserBubble text={pendingText} />
            <p className="text-sm text-slate-500">Working...</p>
          </div>
        )}
        <div ref={bottom} />
      </div>
      {error && (
        <p role="alert" className="border-t border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">
          {error}
        </p>
      )}
      <form onSubmit={submit} className="border-t border-slate-200 bg-white p-3">
        {awaitingDecision && (
          <p className="mb-2 text-xs text-indigo-700">
            Approve or reject the pending action above to continue.
          </p>
        )}
        <div className="flex gap-2">
          <textarea
            ref={input}
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            aria-label="Message"
            disabled={loading || !state || awaitingDecision}
            placeholder="Name the plan to investigate, e.g. Check plan PLN-1001 for revenue leakage"
            className="min-h-0 flex-1 resize-none rounded-md border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <button
            type="submit"
            disabled={busy || loading || awaitingDecision || !draft.trim()}
            className="self-end rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  )
}

