# Revenue Leakage Agent

Design of the Revenue Leakage Agent: a stateful chat agent that investigates billing anomalies and proposes corrections, which it applies to a sandbox only after human approval.

Related documents:

- [data-analysis.md](data-analysis.md): analysis of the data in `data/` and its ambiguities (A1 to A15).
- [api.md](api.md): the API contract (endpoints, events, flows, real backend details).
- [agent.md](agent.md): generic know-how for a LangGraph agent; the `D#` here override it.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI, Python 3.12 with uv, ruff, pytest |
| Frontend | Vite + React + TypeScript, Tailwind v4, Vitest, Playwright (one e2e test, D39) |
| Agent | LangGraph, OpenAI-compatible LLM through `get_llm()` (see [agent.md](agent.md)) |
| Commands | Makefile (GNU make) |
| CI | GitHub Actions: `check` job (`make install` + `make check`) and `e2e` job (`make e2e`, D39) |

---

## Decisions

Format: `### D#. Title`, the decision, why, and the alternatives that were rejected.

Precedence: the fixed billing rules (D20: billing in advance) and what the data decides win over any assumption; an assumption that changes a plan's result lowers its confidence (D20, D37).

Two things are called "settings": the app configuration (`app/config.py`, `settings.state_dir`, `AGENT_BACKEND`, environment variables) and the **analysis settings** (D11, D24), which the user changes in the panel. In this document, "settings" without a qualifier means the analysis settings.

### D1. Own graph `agent` / `tools` / `human_review`, no router

The graph has three nodes: `agent` (the LLM with the tools), `tools` (runs them) and `human_review`. No router classifies the user's intent: the LLM picks the tools. `human_review` calls `interrupt()` before every `apply` and `rollback`, and it is the only thing that stops the graph. Before interrupting it checks that the action can run (D34); if it cannot, there is no card and the tool returns the error.

The approval is the UI card, not the chat: a "yes" in the chat only makes the LLM call `apply`, and `apply` checks that an approval was recorded before writing. A rejection also goes through `tools`: the decision is stored in `reviews` by `tool_call_id` and the wrapper returns `rejected_by_user` to the LLM, so every tool call has its `ToolMessage` and the agent knows nothing was written.

Why: the product is a conversational agent, not a pipeline, and the one hard rule ("no action without explicit approval") lives in the graph and in the tool, not in the LLM's judgment.

Rejected:

- **An intent router before the agent** (a node that classifies each message as question, investigation, proposal or approval and routes it to a subgraph): it turns the dialogue into a fixed pipeline and fails on mixed or follow-up messages; the LLM with tools already picks what to do, and the only thing that must stop it is `human_review`.
- **A "yes" in the chat as the approval**: it depends on the LLM reading the text right; the approval is the card, and `apply` checks that it was recorded.
- **Handling a rejection outside `tools`** (going straight back to `agent` without running the tool): it leaves the tool call without a `ToolMessage` (the provider rejects every later call) and with no record in `reviews`.

### D2. State = `messages` + structure written only by tools

Besides `messages`, the thread state has fields that only tools write, through `Command(update=...)`: active plan and customer, `findings`, `proposals`, `reviews` and conversation flags (for example `freshness_asked`). Whatever the agent must not repeat lives in the state, never only in the prompt. Every turn a summary goes into the prompt: that state, the current settings (D24) and the active sandbox actions (the sandbox is global, D5), with their `action_id`.

Why: the LLM cannot invent facts in the state, follow-up references ("what about the other months?", "that plan") resolve against verifiable data, and a flag in the state survives even if the history is trimmed.

Rejected:

- **Conversation state only in the prompt or the history** (relying on prompt instructions or the history to remember the active plan, the findings or what was already asked): the LLM repeats questions and loses references.

### D3. `SqliteSaver` in the app, `MemorySaver` in tests

`build_graph(checkpointer, llm)` receives both dependencies. The app uses `SqliteSaver`; tests use `MemorySaver` and `ScriptedLLM`.

Why: `uvicorn --reload` restarts the process, and an in-memory checkpointer loses the pending interrupt, leaving an orphan approval card.

Rejected:

- **`MemorySaver` in the app too**: `uvicorn --reload` wipes the pending interrupt.

### D4. Saver, sandbox and settings under `settings.state_dir`

The checkpointer database, the sandbox JSON files and the analysis settings (D24) live under `settings.state_dir` (`STATE_DIR`, default `.state/`), never at fixed paths. `data/` is read-only.

Why: tests and each environment point to their own folder without touching the app state or another run's sandbox, and `make reset` puts everything back to the initial state.

Rejected:

- **Hard-coded paths for the saver and the sandbox** (for example `data/sandbox/`): tests and the app would overwrite each other's state.

### D5. Sandbox: append-only ledgers, global

The sandbox has one ledger per writable file of `data/`: `invoices.json` (make-goods), `credit_memos.json` (memos) and `billing_plans.json` (amendment versions), each with the schema of its counterpart in `data/` plus the fields of D25, and `audit_log.json`. Linking and dismissing an unmatched record (D22) write no ledger: they live only in `audit_log.json`. Ledgers are append-only: `rollback` marks the action and its records as rolled back and never deletes. IDs are sequential per type (D25). The sandbox is global, not per thread: what is applied in one conversation shows in all of them.

Why: the audit log and the ledgers tell the whole story, including what was rolled back, and a schema equal to `data/` lets the same code read them (D6). Global because the sandbox stands for the billing system, and there is only one.

Rejected:

- **A rollback that deletes records**: it loses the history, and the audit log stops matching the ledgers.
- **A sandbox per thread**: the sandbox stands for a single billing system; an applied correction has to show in every conversation.

### D6. Overlay: what was applied is read together with `data/`

The read tools (`load_plan`, `query_invoices`, `list_plans`, `detect_anomalies`) and `GET /api/plans` read `data/` plus the active sandbox records, marked `source: "sandbox"` (records from `data/` carry `source: "data"`), and apply the active links and dismissals from the audit log. Rolled-back records are not read.

Why: after a correction is applied, re-running the check takes it into account (the anomaly is resolved), and the agent can say what came from the original data and what from the sandbox.

Rejected:

- **Read tools that see only `data/`**, with the applied records queried separately: the check after a correction would still show the anomaly as open.

### D7. Make-good with an explicit `period_start`, one per period

`propose_make_good_invoice` receives the `period_start` of the period it covers, besides `plan_id` and `amount` (no `reason`, see D15). Each make-good covers a single period; several missing periods are several proposals. The detector attributes a make-good to its `period_start`, not to the period of its `issue_date` (the day it is applied, D25).

Why: with an explicit period the overlay (D6) knows which gap the invoice closes without reading free text, and each period is approved and rolled back on its own.

Rejected:

- **A make-good for several periods, or with the period only in the `reason` text**: the overlay cannot tell which gap it closes, and a single period cannot be approved or rolled back on its own.

### D8. Rollback by ID, only of the last active action

`rollback(action_id)` receives the action ID and accepts it only if it is the last active (not rolled back) action in the sandbox. Otherwise it returns an error that names the last one. It goes through `human_review` like `apply` (D1) and, like every action, only when the user asks (D17).

Why: an explicit ID avoids rolling back the wrong action, and rolling back in order (a stack) avoids leaving the sandbox inconsistent, for example a credit memo on an amendment that was already rolled back.

Rejected:

- **Rolling back any action by ID in any order, or "the last one" without naming it**: out of order it leaves broken dependencies; without an ID the wrong action can be rolled back.

### D9. Synchronous API `{events, state}`, threads created by the server

Every chat call is synchronous and returns `{events, state}` (D28). `events` are typed events derived from `messages`; agent errors (LLM down, invalid response) also come as an `error` event with HTTP 200, not as a 500. `state.actions` is the audit log. The server creates threads (`POST /api/threads`); the client does not invent IDs. No streaming.

Why: a contract that is simple to implement and test within the project's scope; the frontend does not parse LangChain messages, and an LLM error shows in the chat without breaking the conversation.

Rejected:

- **Streaming (SSE) and client-generated thread IDs**: more complexity than the scope justifies, and a client thread may not exist on the server.

### D10. Approval through the API with `interrupt_id`, 409 when it does not match

The decision (`approve` or `reject`) carries the card's `interrupt_id`. The API returns 409 if the `interrupt_id` does not match the pending interrupt (or there is none), if a chat message arrives while the thread has a pending action, and if a settings change arrives while any thread has one (settings are global, D11).

Why: an old or duplicate card does not approve another action, and changing the state while an interrupt is pending reschedules it and changes its id (see the gotchas in [agent.md](agent.md)).

### D11. Global settings, only from the panel

Settings are global, not per plan: the cutoff date (`as_of`), data freshness (`data_up_to_date`) and every analysis assumption the data does not decide. The concrete list is in D20 and D21; storage and contract in D24. Each one is `null` until set; meanwhile the detector uses the default. They are changed only from the panel (`PUT /api/settings`), never from the chat: if one is missing, the agent asks the user to set it there. When they change, findings become stale and the agent recomputes them before using them.

Fixed rules (those of D20 and what the data decides) are not settings. Billing in advance is one.

Why: an assumption that changes the result has to be explicit, visible and auditable, not something the LLM infers from the conversation; and findings that depend on assumptions that changed cannot keep being used without notice.

Rejected:

- **Assumptions set from the chat, or per-plan settings**: they stay implicit in the history, the LLM can infer them wrong, and per-plan settings multiply combinations the data does not call for.

### D12. The plan list comes from the API

The user names the plan in the chat. When the UI needs the plan list it asks `GET /api/plans`; the frontend never hard-codes plans.

Why: the list includes what was applied in the sandbox (D6) and does not drift from the data.

Rejected:

- **A selector with hard-coded `plan_id`s**: it drifts from the data and the sandbox.

### D13. Plan amendment = new version of the same `plan_id`

`propose_plan_amendment` creates a new version of the same `plan_id` in the plans ledger, effective from an `effective_from` (D26). The plan's identity fields (`plan_id`, `customer_name`, `start_date`, `currency`) cannot change; a `change_set` that touches them is rejected. A plan replaced by another through `amends` (`PLN-2001`) cannot be amended: the replacement is amended instead.

Amendments that already come in `data/` as a separate plan with `amends` (`PLN-2001-A1`) are read as they are, per D20; they are not turned into versions.

Why: invoices and credit memos keep pointing to the same `plan_id`, and changing the currency or the start date is not an amendment but another contract. Keeping the shape of `data/` avoids rewriting the original data.

Rejected:

- **An amendment as a new plan with a derived ID** (for example `PLN-2001-A2`): the plan's invoices would be spread across IDs and the version chain would have to be rebuilt.

### D14. Endpoints under `/api`, mock or real by `AGENT_BACKEND`

| Method and path | What it does |
|---|---|
| `GET /api/health` | Liveness check |
| `GET /api/plans` | Plan list with the overlay (D6, D12) |
| `GET /api/settings`, `PUT /api/settings` | Read and change the settings (D11, D24); the `PUT` returns 409 if any thread has a pending action, and 422 for an invalid value |
| `POST /api/threads` | Creates a thread (D9); returns `{thread_id, events: [], state}` |
| `GET /api/threads/{id}` | `{events, state}` of the whole history, to reload the UI |
| `POST /api/threads/{id}/messages` | The user's `{text}`; returns `{events, state}` of the turn |
| `POST /api/threads/{id}/decisions` | `{interrupt_id, decision}` on the pending action (D10); returns `{events, state}` |
| `GET /api/graph` | The graph as Mermaid text |

Routes with `{id}` return 404 if the thread does not exist. The `AGENT_BACKEND` environment variable (`mock` / `real`, in `app/config.py`) picks the implementation (D29); when unset, `real` applies.

Why: the frontend can be developed against the mock without an LLM, and an unconfigured deployment never ends up on the mock by accident.

Rejected:

- **Mock by default when the variable is unset**: a misconfigured deployment would serve fake data without warning.

### D15. Tools

The domain tools plus `list_plans`, `detect_anomalies`, `get_concept` and two `propose_*` to link or dismiss an unmatched record; the signatures are in D27. `apply(proposal_id)` and `rollback(action_id)` go through `human_review` (D1). The `propose_*` tools take no `reason`: the code builds the evidence and the reason from the detector finding. Wrappers return domain errors to the LLM as a result (`{"status": "error", "error": ...}`), not as an exception.

Why: a free-text `reason` would be written by the LLM, and what justifies a proposal is the detector finding behind it (D17). `apply` receives the ID of an already validated proposal, not a draft the LLM could alter.

Rejected:

- **A free-text `reason` in the `propose_*` tools**: the reason comes from the detector finding, not from text the LLM could invent.
- **`apply(action_draft)` receiving the full draft**: the LLM could alter the draft between the proposal and the approval.

### D16. Knowledge base by key, no embeddings

The key domain concepts, the fixed rules, the settings and the detector statuses go in a markdown file, one chunk per term, which `get_concept` retrieves by key (D32). Plans and invoices are also retrieved by key (`load_plan`, `query_invoices`). No embeddings.

Why: the corpus is small and every term has an exact name; similarity can bring back the wrong chunk, while a key lookup is deterministic and citable.

Rejected:

- **Embedding retrieval** of concepts, plans and invoices from a vector store: a small corpus with exact keys, where similarity can bring back the wrong record or term.

### D17. Proposals only on request, revalidated against the detector

The agent never proposes or runs an action on its own initiative, only when the user asks. An amount difference with no known cause is presented as a labeled hypothesis, and the agent waits. If the data cannot verify something, it says so and does not infer it.

Every `propose_*` and every `apply` revalidates against the recomputed detector:

| Proposal | Only if |
|---|---|
| Make-good | The period has a `missing` or `underbilled` finding, the plan has `high` confidence, and the amount is in (0, shortfall] |
| Credit memo | The invoice is in an `overbilled` period of a plan with `high` confidence, and the amount is in (0, excess] |
| Amendment | The `change_set` only touches `total_value`, `cadence`, `entitlements` or `notes`, with a valid `effective_from` (D13, D26) |
| Link | The record is in the residue with reason `no_plan`, and the plan is one of its candidates |
| Dismiss | The record is in the residue with reason `no_plan` |

A `pending_decision` or `no_data` period accepts neither a make-good nor a credit memo.

Why: nothing is written without approval, and on top of that nothing can be proposed that the data does not back. Revalidating in `apply` covers the data, the sandbox or the settings changing between the proposal and the approval.

Rejected:

- **Proposals on the agent's initiative**, as soon as it detects an anomaly: the user is in charge; the agent presents findings and hypotheses and proposes only on request.

### D18. Interface: chat with cards and a side panel

- **Chat:** markdown, cards for findings (with totals), proposals and approvals (Approve / Reject), and a collapsible trace per answer (the turn's tool calls and results).
- **Side panel:**
  - **Settings:** each with its value, its default and whether it is set, plus `data_through` (read-only). Saving does `PUT /api/settings`; a 409 shows as "an action is pending".
  - **Plans:** from `GET /api/plans`; a click writes a request about that plan into the chat input (for example "Check plan PLN-1001 for revenue leakage") without sending it.
  - **Audit log:** `state.actions`.
- No graph diagram in the UI (it stays in `GET /api/graph`).
- The UI is in English; the agent answers in the user's language.
- The `thread_id` is kept in `localStorage`; if the server returns 404, a new thread is created.

Why: the product is a chat interface; the panel holds what is not conversation (the settings of D11 and the audit log of D25), and the plan shortcuts write into the chat so the user keeps naming the plan there (D12).

Rejected:

- **The graph's Mermaid diagram in the UI**: it does not help the user of the conversation; it stays in `GET /api/graph` for demos.
- **Plan shortcuts that send the message**: the user has to be able to adjust the request; the shortcut only writes into the input.

### D19. Deterministic detector `detect_anomalies`

`detect_anomalies` is deterministic code, no LLM. It works in the plan's currency and net of each invoice's credit memos. It returns:

- **Every expected period** with its status: `ok`, `missing` (no invoice), `underbilled`, `overbilled`, `pending_decision` (D22) or `no_data` (D21), and its expected, invoiced, credited, net and difference amounts, with the invoices and memos behind them.
- **Per-invoice flags** that do not change amounts: `unpaid`, `currency_mismatch` (with the conversion applied) and the days between the period start and the issue date.
- **`totals`** per plan and overall. `no_data` periods do not count.
- **`confidence`** per plan (`high` / `low`, with the settings that lower it, D20).
- **`assumptions`**: the cutoff (`as_of`, whether it is inferred or set, and `data_through`), and every rule and setting (set or default) the result depends on.
- **The `unexplained` residue**: every invoice or memo that could not be placed in a period, with its reason (`no_plan` with its candidates, `no_fx_rate`, `outside_term`, `after_as_of`, `orphan_memo`, `dismissed`). No invoice or memo disappears silently.

The LLM only reasons about the residue, as a labeled hypothesis (D17). Every `propose_*` recomputes the detector and validates against it.

Why: amounts are verifiable and testable without an LLM (the ground truth table below), and the explicit residue forces an account of every record, including those that do not fit.

Rejected:

- **Letting the LLM compute the anomalies** from the plans and invoices: amounts would be neither reproducible nor testable, and records could be skipped.

### D20. Assumptions: fixed rules and settings

Derived from [data-analysis.md](data-analysis.md). By default, the assumptions that make the amounts add up in the analysis apply. What the data decides is a fixed rule. What it does not decide is a setting (D11): the detector runs every reading, and if they give different results for a plan (with the other settings as they are), that plan's findings are `confidence: low` while the setting is not set, and accept no proposals (D17).

**Fixed rules:**

| Rule | Source |
|---|---|
| Billing in advance: each invoice belongs to the period that contains its `issue_date` (except make-goods, D7) | Fixed rule of the domain; `PLN-1001` says "billed in advance" in `notes` |
| 12-month term from `start_date`, for every plan | All four plans have a 12-month term; the annual one says so in `notes` (A4) |
| Amount per period = `total_value` / periods in 12 months, with the plan version in force at the period start (D26) | `PLN-1001` 7,000 and `PLN-2001-A1` 28,000 match `notes`; `PLN-3001` 120,000 / 12 months |
| A plan with `amends` replaces the amended plan from its `start_date`, and its `total_value` covers 12 months from there | 4 × 28,000 = 112,000 matches the `notes` of `PLN-2001-A1` (A7) |
| Proration of a partial period: by whole months if the span is whole months, by days otherwise | Reproduces the analysis: `PLN-2001` May–June = 47/92 = 10,728.26 (A5) |
| Currency: the rate of the invoice's `issue_date`, only in the published direction; with no rate, the invoice goes to the residue (`no_fx_rate`) | The only available rate (A11, A15) |
| Memos are subtracted from their invoice, in the same period; `amount` is positive and reduces | `CR-001` leaves `INV-2001` at 28,000 net (A11) |
| An `unpaid` invoice counts as invoiced, with a flag | It is a collection problem, not leakage (A12) |
| Late issuing is a flag and does not change amounts | A13 |
| Entitlements are not verified: there is no price per entitlement; the agent says so | D17, A14 |

**Settings** (besides `as_of` and `data_up_to_date`, D21):

| Setting | Values (default first) | Plans whose readings differ | Ambiguity |
|---|---|---|---|
| `period_calendar` | `anchored` (to `start_date`) / `calendar` | `PLN-2001`, `PLN-3001` | A6, A10 |
| `amendment_proration` | `prorate` / `full_period` (the period of the cut, fully charged to the amended plan) | `PLN-2001` | A5 |
| `auto_renewal` | `false` / `true` (when the term ends, another 12-month term repeats with the latest plan version) | `PLN-2001-A1`, `PLN-3001` with an `as_of` after the end of their term (with the cutoff at `data_through`, none) | A2 |

Which plan `INV-4001` belongs to (A8) is not a setting but an action (D22). The cause of the `PLN-3001` difference (A9) is not decided by the detector: it reports `underbilled` and the agent presents the cause as a hypothesis.

Why: a reasonable default allows analysis without configuring anything, but `confidence: low` blocks proposing a correction that depends on an assumption nobody confirmed.

Rejected:

- **Silent defaults with high confidence** (applying the default assumptions and treating findings as firm): corrections would be proposed that depend on an assumption nobody confirmed.
- **Billing in arrears as an alternative reading** (a setting letting each invoice cover the previous period): billing in advance is a fixed rule.
- **The scope of the amendment as a setting** (reading `PLN-2001-A1.total_value` as the new total of the whole Vertex Dynamics contract since 2025-05-15, A7): the data decides it, since 4 × 28,000 = 112,000 matches `notes` from the effective date, and the other reading matches no amount.
- **Always prorating by days**: it loses the whole-month rule; a span of whole months (for example a quarter cut at a month boundary) is prorated by months, not days.
- **Converting with the inverse rate or the nearest rate** (`1 / rate` for USD→EUR, or the closest date when there is no exact one): the data only publishes EUR→USD on 2025-08-20, and inferring another rate contradicts "if the data cannot verify it, do not infer it" (D17).

### D21. Cutoff: `as_of` and `data_through`

`as_of` is a setting (`null` = today). `data_through` is computed: the last `issue_date` in `data/` (2026-06-30). Periods that start up to `as_of` are expected. Those that start after `data_through` are `no_data`, not `missing`. With `data_up_to_date: true` the gap is taken as real and those periods count as `missing` or `underbilled`.

If there are expected periods in the gap and `data_up_to_date` is not set, the agent asks once per conversation (`freshness_asked`, D2) whether the data is up to date, with the amount at stake, and asks the user to set it in the panel (D11).

Why: with 4.6 months without data, counting that gap as missing inflates the leakage by 35,000 without evidence (A3); asking once avoids repeating the question every turn.

Rejected:

- **Counting the gap without data as missing** (expecting billing up to today and counting every period without an invoice as `missing`): it adds 35,000 of leakage with no evidence that the data is up to date; it is `no_data` unless the user confirms.

### D22. Unmatched records

An invoice with no `plan_id` (or with one that does not exist) goes to the residue with reason `no_plan` and its candidates: the plans of the same `customer_name` whose current term contains its `issue_date`. If it falls in a candidate's period that has a finding (`missing`, `underbilled`, `overbilled`), that period becomes `pending_decision` and accepts no proposals until it is decided. Linking and dismissing are sandbox actions, with a proposal and an approval (D15, D17), recorded in `audit_log.json` and applied by the overlay (D6). A linked invoice is attributed to the period of its `issue_date` in the chosen plan. A dismissed invoice stays in the residue with reason `dismissed`.

Why: the link changes the finding's amount (`PLN-2001-A1` Oct–Dec goes from −28,000 to −13,000), so proposing a make-good before deciding could bill twice.

Rejected:

- **Assigning `INV-4001` automatically** to the only candidate by customer and date: it changes the finding's amount and the description ("Payment with no contract reference") does not confirm it.
- **A link as a new version of the invoice in the ledger** (a copy of `INV-4001` with the `plan_id` filled in, in the sandbox `invoices.json`): it duplicates an invoice from `data/` and the overlay would have to decide which one wins; links and dismissals live only in the audit log (D5).

### D23. Recompute after `apply` / `rollback`

After `apply` or `rollback`, only the affected plan is recomputed (for a link or a dismissal, the candidate plans), and only if no findings are stale (D24). If any are stale, nothing is recomputed: the next `detect_anomalies` recomputes everything.

Why: the agent can confirm that the anomaly was resolved (D6) without mixing new findings for one plan with old findings for others computed under other settings.

Rejected:

- **Recomputing everything after every action**: with stale findings it would mix results computed under different settings.

### D24. Analysis settings: storage, version and contract

The analysis settings are stored in `{state_dir}/analysis_settings.json`, not in the thread. They have an integer `version` that goes up with every `PUT`; each finding stores the `version` it was computed with, and it is stale if that differs from the current one. `GET /api/settings` returns, per setting, the value (`null` = not set), the default and the valid values, plus `data_through` and `version`. `PUT` receives the values to change (`null` goes back to the default); `as_of` cannot be later than today (422).

Why: settings are global (D11) and have to survive a restart; the version turns "stale" into a comparison instead of an inference.

Rejected:

- **Analysis settings in each thread's LangGraph state**: they are global and have to survive a restart.

### D25. Sandbox records, IDs and audit log

- **IDs:** sequential per type, three digits, never reused even if the action is rolled back: `INV-MG-001` (make-good), `CM-001` (credit memo), `AMD-001` (amendment), `LNK-001` (link), `DSM-001` (dismissal). The `action_id` is the ID of the record the action creates (the one the user sees). Proposals, which live in the thread, use `PRP-001`, sequential per thread.
- **Make-good:** the `invoices` schema, with `issue_date` = the day of the `apply`, `due_date` = `issue_date` + 15 days (the payment term of every invoice in `data/`), the plan's `currency`, `status: "unpaid"`, a code-generated `description`, plus `period_start`.
- **Credit memo:** the `credit_memos` schema, with the invoice's `plan_id`, the plan's `currency`, `issue_date` = the day of the `apply` and a code-generated `reason`.
- **Amendment:** the full plan record with the `change_set` applied, plus `effective_from` (D26).
- **Common sandbox fields** (on every written record): `action_id`, `ledger_state` (`active` / `rolled_back`) and `source: "sandbox"`.
- **`audit_log.json`:** one entry per action: `action_id`, `type`, `proposal_id`, `thread_id`, `plan_id`, the written payload (or the link / dismissal), the evidence and the `reason`, `applied_at`, and `rolled_back_at` if it was rolled back. This is `state.actions`.

Why: readable, stable IDs to quote in the chat and in the rollback; the 15-day term and the dates come from the data, not from an arbitrary value.

Rejected:

- **An `action_id` separate from the record ID** (an `ACT-001` sequence): the user sees and quotes the record ID (`INV-MG-001`); two IDs for the same thing confuse the rollback.

### D26. Effective date of a sandbox amendment

`propose_plan_amendment` receives `effective_from`, which has to be the start of a plan period, within its term, both with the current cadence and with the new one if the `change_set` changes it. Periods that start from `effective_from` use the new version (amount per period = new `total_value` / periods in 12 months of the new cadence); earlier ones use the previous version. The term does not change (12 months from `start_date`). `effective_from` can be in the past: the amendment is retroactive and the detector reflects it.

Why: D13 left open from when a version applies; without a date, an amendment would retroactively change the whole plan. Requiring a period boundary avoids prorations the analysis does not back.

Rejected:

- **An amendment with no effective date** (in force from the plan's `start_date`, or from the day it is applied): the first changes the whole plan retroactively; the second leaves a split period with no proration rule.

### D27. Tool signatures

| Tool | Signature | Writes to the state |
|---|---|---|
| `list_plans` | `()` | — |
| `load_plan` | `(plan_id)`; includes versions and `source` | active plan and customer |
| `query_invoices` | `(plan_id=None, customer_name=None, date_from=None, date_to=None)`; each invoice with its memos | — |
| `fx_convert` | `(amount, from_ccy, to_ccy, on_date)`; error if there is no exact rate in that direction | — |
| `detect_anomalies` | `(plan_id=None)`; `None` = every plan | `findings` (replaces those in scope), active plan if there is one |
| `get_concept` | `(key)`; unknown key → error with the list of keys | — |
| `propose_make_good_invoice` | `(plan_id, period_start, amount)` | `proposals` |
| `propose_credit_memo` | `(invoice_id, amount)` | `proposals` |
| `propose_plan_amendment` | `(plan_id, effective_from, change_set)` | `proposals` |
| `propose_link_invoice` | `(invoice_id, plan_id)` | `proposals` |
| `propose_dismiss_invoice` | `(invoice_id)` | `proposals` |
| `apply` | `(proposal_id)`; goes through `human_review` | `proposals`, `reviews`, `findings` of the affected plan (D23) |
| `rollback` | `(action_id)`; goes through `human_review` | `reviews`, `findings` of the affected plan (D23) |

Why: a closed list lets the contract and the tests be written before the agent.

### D28. API events and state

**Events** (in order, each with a `type` and its payload):

| `type` | Source | Payload |
|---|---|---|
| `user_message` | user message | `text` |
| `assistant_message` | agent text | `text` (markdown) |
| `tool_call` | agent tool call | `tool_call_id`, `name`, `args` |
| `tool_result` | tool result | `tool_call_id`, `name`, `status` (`ok` / `error` / `rejected_by_user`), `result` |
| `findings` | result of `detect_anomalies` | periods, `totals`, `confidence`, `assumptions`, residue |
| `proposal` | result of a `propose_*` | the proposal (D34) |
| `approval_request` | `human_review` interrupt | `interrupt_id`, the action (proposal or rollback) with its evidence |
| `error` | agent failure | `message` |

`findings` and `proposal` come with their tool's `tool_result` (same `tool_call_id`); the trace (D18) is built from `tool_call` and `tool_result`.

**State:** `active_plan_id`, `active_customer`, `findings` (with `stale`), `proposals`, `pending_approval` (`interrupt_id` and action, or `null`), `actions` (global audit log, D25) and `settings_version`.

Why: a single format for mock and real that the UI renders without knowing LangChain; an action's confirmation comes from its `tool_result`, not from the text.

### D29. Mock

With `AGENT_BACKEND=mock`, the service replies with a scripted conversation, with no LLM and no detector, using the same contract (D14, D28) and the ground truth amounts (Base). The script covers every event type: findings for `PLN-1001`, a make-good proposal for 2026-02, an approval card, approve and reject, rollback, an `error`, and the 404 and 409 cases. `GET /api/plans` and `/api/settings` return fixed data consistent with the ground truth. A test checks the mock's Base totals for `PLN-1001` against the detector.

Why: the frontend can be built and tested end to end without the real backend or an LLM key, with the same numbers.

### D30. Cutoff earlier than the data

With an `as_of` earlier than `data_through`, invoices and memos in `data/` issued after `as_of` are not counted: they go to the residue with reason `after_as_of`. `data_through` is clipped to `as_of`. Sandbox records are not filtered by `issue_date` (it is the day of the `apply`, D25): a make-good counts in its `period_start` (D7) and a memo follows its invoice.

Why: the cutoff has to hold on both sides (what is expected and what was invoiced), with no record disappearing (D19); and a correction applied today to a past period has to close it even if the cutoff is earlier than today.

Rejected:

- **Silently ignoring what was issued after the cutoff**: it breaks "no invoice disappears silently" (D19).

### D31. Amounts and rounding

Amounts are computed with `Decimal` and rounded to cents (half-up) per period; totals are the sum of the rounded periods. Dates are `date`, with no time.

Why: it reproduces the ground truth (115,397.26 + 4,602.74 = 120,000) with no floating-point errors.

Rejected:

- **Amounts as `float`**: daily prorations do not always add up exactly.

### D32. Knowledge base file

`backend/app/knowledge.md`, one chunk per `## <key>` section, in English. Keys: `credit_memo`, `plan_amendment`, `make_good_invoice` and `revenue_leakage` (the domain concepts), one per fixed rule of D20, one per setting of D20 and D21, and one per detector status and reason (D19). `get_concept` returns the literal chunk, with its key so it can be quoted.

Why: D16 with a concrete place and format; snake_case keys equal to the names in the code.

### D33. Per-turn state summary

The summary injected every turn (D2) goes in a second system message, after the static system prompt (prompt caching, [agent.md](agent.md)). It includes: active plan and customer, current findings per plan (totals, confidence, stale), pending proposals, active sandbox actions, settings (value or default) and `freshness_asked`.

Why: the agent resolves references ("that plan", "the last action") against the state, and the static prompt stays cacheable.

### D34. Proposal lifecycle

A proposal lives in the thread's `proposals` with a `proposal_id`, type, arguments, evidence, `reason`, the settings `version` it was validated with and a status: `pending` → `applied` (approved and written) or `rejected`; `invalid` if it no longer holds when revalidated in `apply` (it goes back to the agent as an error, with no card). Only a `pending` proposal can be applied, and only once. If the domain rejects an already approved action, the proposal goes back to `pending` ([agent.md](agent.md)).

Why: it prevents applying the same proposal twice, or one that went stale, and shows where each one ended up.

Rejected:

- **Proposals shared between threads** (stored in the global sandbox to apply them from any conversation): the approval belongs to the conversation that asked for it (the thread's interrupt, D1, D10); what is global is what was applied, not what was proposed.

### D35. Contract: what was added and what was not

- `GET /api/health` returns `{status, agent_backend}`: it is how to tell whether the mock or the real backend is running (testing and deployment).
- `state` does not expose `freshness_asked`: it is an internal flag (D2); "asked only once" is checked through the thread's events.
- `Totals` and `Period` carry no `currency`: the UI takes it from `GET /api/plans` by `plan_id` and omits it from the overall total if plans differ (all four plans in `data/` are USD).
- `settings.data_dir` (`DATA_DIR`, default `data/`) exists, to run test fixtures with their own data.
- UI: the chat input is disabled while `state.pending_approval` is not `null` (D10); saving settings is not disabled and shows the 409. There is no new-conversation button (use another private window or clear `localStorage`). An empty message returns 422.
- A thread can roll back the last active action even if another thread applied it (D5, D8).

Why: the minimum needed to run and test without growing the contract beyond what the UI uses.

Rejected:

- **`currency` in `Totals` and `Period`**: the UI already takes it from `GET /api/plans` by `plan_id` and all four plans are USD; it grows the contract with no real need.
- **`freshness_asked` in the API state**: it is an internal agent flag (D2); it is checked through the thread's events.
- **A "New chat" button**: D18 does not call for it; use another private window or clear `localStorage`.

### D36. Real backend: contract behaviors

The details the real backend fixed when implementing D9, D23 and D28 (a summary in the `tool_result` of `detect_anomalies`, the synthetic `recheck-...` recompute, already decided cards re-emitted on reload, in-memory `error` events, `default: null` for `data_up_to_date`) are in the "Real backend details" section of [api.md](api.md).

Why: they are contract and live where the frontend reads them; this entry is the pointer, so they are not duplicated.

### D37. Confidence: it only drops if the setting changes the result

The rule of D20 holds: a plan is `low` only if some alternative value of an unset setting changes its result. With `period_calendar = calendar`, `PLN-2001` becomes `high` because `amendment_proration` no longer changes anything (the 2025-07-01 cut is a quarter boundary).

Why: if the result does not depend on the assumption, nothing unconfirmed justifies blocking proposals (D20).

### D38. Agent behaviors accepted after testing

Found while testing against the real LLM, all minor and with no write without approval; the prompt is not changed:

- An imperative request ("Issue a credit memo for ...", "Link INV-4001 to ...") chains `propose_*` and `apply` in the same turn and ends at the card. It counts as asking to apply (D17); the approval is still the card (D1) and `state.actions` does not change until Approve. "Propose ..." only proposes.
- An out-of-order rollback can be answered in text from the state summary (naming the last action) without calling `rollback`: no card and no write.
- Sometimes the text is in Spanish for an English prompt (prompt rule 13) or quotes a finding's internal key (`PLN-1001:missing:2026-02-01`). Known LLM limitations, not structural ones.

Why: the hard rule (nothing is written without the card) holds in every case; hardening the prompt to polish these observations risks regressions with no safety gain.

Rejected:

- **Hardening the prompt for these minor observations** (new rules to force the language, hide finding keys, not chain `propose_*` and `apply`, and require the tool on an out-of-order rollback): no case writes without the card; the imperative request is equivalent to asking to apply, and more rules risk regressions.

### D39. One end-to-end smoke test: Playwright against the mock, in its own CI job

A single Playwright test (`frontend/e2e/approval-flow.e2e.ts`, Chromium only) walks the mock's scripted flow (D29) in the real UI: investigate PLN-1001 (-5,500), propose the February make-good, apply, check that the audit log is still empty before Approve, approve (`INV-MG-001`, +1,500), roll back and approve (-5,500). Playwright starts its own backend (`AGENT_BACKEND=mock`, `STATE_DIR` in the OS temp dir) and Vite dev server on ports 8100 / 5180, never reusing a running server. It runs with `make e2e` (`make e2e-install` downloads Chromium once) and in a separate `e2e` job of the CI, which uploads the report and trace when it fails; it is not part of `make check`.

Why: pytest and Vitest test each side alone; nothing else checks them together (the `/api` proxy, the cards wired to `state`, the approval round trip, totals shown from the API). The mock is deterministic and uses the ground-truth numbers, so one test covers the main flow with no LLM and no flakiness from model text.

Rejected:

- **E2E against the real LLM**: not deterministic, costs money and needs a secret in CI; `make eval-llm` already covers the real LLM at the structure level.
- **Several browsers or many scenarios**: maintenance and CI time with no gain at this size; edge cases belong in the Vitest and pytest tests.
- **Inside `make check`**: it needs a browser download (about 300 MB) and two servers, which slows the local loop for every change.
- **Against a production build (`vite preview`)**: the dev server is what the proxy config and `make dev` use; a build step adds time and tests nothing extra here.

---

## Detector ground truth

The detector test table, taken from the expected vs invoiced tables of [data-analysis.md](data-analysis.md) (4.1 to 4.5), with `as_of = data_through` (2026-06-30) and the defaults of D20. Amounts in USD. The tests (`backend/tests/test_detector.py`, `test_actions.py`) encode these rows.

### Base

| Plan | Period | Expected | Invoiced | CM | Net | Diff. | Status |
|---|---|---:|---:|---:|---:|---:|---|
| `PLN-1001` | 2025-12 (`INV-1001`) | 7,000 | 7,000 | — | 7,000 | 0 | `ok` |
| `PLN-1001` | 2026-01 (`INV-1002`) | 7,000 | 7,000 | — | 7,000 | 0 | `ok` (`INV-1003` goes to the residue) |
| `PLN-1001` | 2026-02 | 7,000 | 0 | — | 0 | −7,000 | `missing` |
| `PLN-1001` | 2026-03 (`INV-1004`) | 7,000 | 7,000 | — | 7,000 | 0 | `ok` |
| `PLN-1001` | 2026-04 (`INV-1005`, flag `unpaid`) | 7,000 | 7,000 | — | 7,000 | 0 | `ok` |
| `PLN-1001` | 2026-05 (`INV-1006`) | 7,000 | 8,500 | — | 8,500 | +1,500 | `overbilled` |
| `PLN-1001` | 2026-06 (`INV-1007`) | 7,000 | 7,000 | — | 7,000 | 0 | `ok` |
| `PLN-2001` | 2025-05-15 – 06-30 (prorated 47/92 by the cut) | 10,728.26 | 0 | — | 0 | −10,728.26 | `missing` |
| `PLN-2001-A1` | 2025-07-01 – 09-30 (`INV-2001`: 30,000 EUR × 1.10, `CR-001`) | 28,000 | 33,000 | −5,000 | 28,000 | 0 | `ok` (flag `currency_mismatch`) |
| `PLN-2001-A1` | 2025-10-01 – 12-31 | 28,000 | 0 | — | 0 | −28,000 | `pending_decision` (`INV-4001`) |
| `PLN-2001-A1` | 2026-01-01 – 03-31 (`INV-2002`) | 28,000 | 28,000 | — | 28,000 | 0 | `ok` |
| `PLN-2001-A1` | 2026-04-01 – 06-30 (`INV-2003`) | 28,000 | 28,000 | — | 28,000 | 0 | `ok` |
| `PLN-3001` | 2025-07-15 – 2026-07-14 (`INV-3001`) | 120,000 | 100,000 | — | 100,000 | −20,000 | `underbilled` |

| Plan | Expected | Net | Diff. | Confidence |
|---|---:|---:|---:|---|
| `PLN-1001` | 49,000 | 43,500 | −5,500 | `high` |
| `PLN-2001` | 10,728.26 | 0 | −10,728.26 | `low` (`period_calendar`, `amendment_proration`) |
| `PLN-2001-A1` | 112,000 | 84,000 | −28,000 | `high` |
| `PLN-3001` | 120,000 | 100,000 | −20,000 | `low` (`period_calendar`) |
| **Total** | **291,728.26** | **227,500** | **−64,228.26** | |

- **Residue:** `INV-1003` (400 EUR, Alder Creek Logistics, 2026-01-20), reason `no_fx_rate`, no candidates; `INV-4001` (15,000 USD, Vertex Dynamics, 2025-11-20), reason `no_plan`, candidates `[PLN-2001-A1]`.
- **Freshness gap:** none (`as_of = data_through`); the agent does not ask.
- **Accepted proposals:** make-good on `PLN-1001` 2026-02 (up to 7,000), credit memo on `INV-1006` (up to 1,500), and linking or dismissing `INV-4001`. None on `PLN-2001` or `PLN-3001` (`low`), nor on `PLN-2001-A1` Oct–Dec (`pending_decision`).

### Variants

Each one changes a single assumption from the Base; a changed setting is set and stops lowering the confidence.

| Variant | `PLN-1001` | `PLN-2001` | `PLN-2001-A1` | `PLN-3001` | Total | What changes |
|---|---:|---:|---:|---:|---:|---|
| Base | −5,500 | −10,728.26 | −28,000 | −20,000 | **−64,228.26** | |
| `period_calendar = calendar` | −5,500 | −10,846.15 | −28,000 | −20,000 | **−64,346.15** | `PLN-2001`: April–June prorated 47/91 = 10,846.15 `missing`; becomes `high` (the amendment's cut falls on a quarter boundary, so `prorate` and `full_period` give the same, D37). `PLN-3001`: 2025-07-15 – 12-31 expected 55,890.41 `overbilled` (+44,109.59) and 2026-01-01 – 07-14 `missing` (−64,109.59); becomes `high` |
| `amendment_proration = full_period` | −5,500 | −21,000 | −28,000 | −20,000 | **−74,500** | `PLN-2001`: May–August in full, 21,000 `missing`; stays `low` because of `period_calendar` |
| `period_calendar = anchored`, `amendment_proration = prorate` (set) | −5,500 | −10,728.26 | −28,000 | −20,000 | **−64,228.26** | All `high`: make-goods on `PLN-2001` (up to 10,728.26) and `PLN-3001` (up to 20,000) are accepted, besides those of the Base |
| `auto_renewal = true` | −5,500 | −10,728.26 | −28,000 | −20,000 | **−64,228.26** | Nothing: no term ends before the cutoff |
| `as_of = 2026-11-18`, `data_up_to_date = null` | −5,500 | −10,728.26 | −28,000 | −20,000 | **−64,228.26** | `PLN-1001` 2026-07 … 2026-11 `no_data`; the agent asks once about freshness, with 35,000 at stake |
| `as_of = 2026-11-18`, `data_up_to_date = true` | −40,500 | −10,728.26 | −28,000 | −20,000 | **−99,228.26** | `PLN-1001` July–November `missing`. Now `auto_renewal` changes the result of `PLN-2001-A1` and `PLN-3001`: they become `low` until it is set (`PLN-3001` already was) |
| `as_of = 2026-11-18`, `data_up_to_date = true`, `auto_renewal = true` | −40,500 | −10,728.26 | −84,000 | −140,000 | **−275,228.26** | `PLN-2001-A1` 2026-07-01 and 2026-10-01 `missing` (−28,000 each); `PLN-3001` second year `missing` (−120,000) |
| `as_of = 2026-02-28` | −7,000 | −10,728.26 | −28,000 | −20,000 | **−65,728.26** | `PLN-1001` only December–February; `INV-1004` … `INV-1007` and `INV-2003` go to the residue (`after_as_of`); `PLN-2001-A1` only Jul–Sep and Jan–Mar (`INV-2002`); `INV-4001` stays `no_plan` (D30) |
| Action: link `INV-4001` to `PLN-2001-A1` | −5,500 | −10,728.26 | −13,000 | −20,000 | **−49,228.26** | Oct–Dec 15,000 vs 28,000 `underbilled`; only `INV-1003` stays in the residue |
| Action: dismiss `INV-4001` | −5,500 | −10,728.26 | −28,000 | −20,000 | **−64,228.26** | Oct–Dec `missing` (accepts a make-good); `INV-4001` in the residue with reason `dismissed` |
| Action: make-good `PLN-1001` 2026-02 for 7,000 | +1,500 | −10,728.26 | −28,000 | −20,000 | **−57,228.26** | 2026-02 `ok` with `INV-MG-001` (`source: "sandbox"`); after the rollback, back to the Base |
| Action: credit memo `INV-1006` for 1,500 | −7,000 | −10,728.26 | −28,000 | −20,000 | **−65,728.26** | 2026-05 `ok` with `CM-001`; the +1,500 excess becomes a −7,000 shortfall |

---

## System prompt

The rules of the agent's system prompt: those of [agent.md](agent.md) adapted to the domain, plus this project's own. The prompt text in `backend/app/agent/prompt.py` is written from this list.

1. Every fact and number comes from the tools. Never compute amounts or conversions in your head.
2. Cite the evidence: record IDs, values and the calculation. The evidence of a finding stays with that finding.
3. Always state the assumptions: the cutoff (and whether it was inferred) and every rule or setting the result depends on.
4. The data-freshness answer holds for the whole conversation; do not ask again for other plans.
5. Settings are changed in the panel, not in the chat: ask the user to set them there.
6. Never propose or run an action on your own initiative; only when the user asks. A "yes" in the chat is not the approval: call the action and the system shows the approval card.
7. Confirm an action only from its tool result, quoting the real ID it returned. If the user rejected it, say nothing was written and adapt.
8. After an action, re-run the check before saying something is resolved.
9. Anything the tools did not establish goes in a clearly labeled hypotheses section, citing IDs, never with a proposal. No hypotheses for periods reported ok.
10. If the data cannot verify something, say so; do not infer it.
11. Billing only: an out-of-domain question gets one sentence and a redirect.
12. Short answers; never dump internal state or detector codes.
13. Answer in the user's language.

---

## Gotchas

Implementation details with no real alternative, which is why they are not `D#`: library quirks, data traps, platform issues. Grouped by area; read the area's list before changing that part of the code. Add one whenever a bug turns out not to be obvious.

### Contract and mock

- The mock returns the amounts of the detector table (ground truth, Base), so the UI is tested with the same numbers as the real backend.
- Mock and real implement the same interface and return the same models from `schemas.py`: the routes do not know which one runs.

### Domain

Data:

- `INV-4001.plan_id` is `""`, not `null` or missing: treat the empty string as no plan.
- `CR-001.amount` is positive even though a credit memo "is a negative invoice": subtract it, do not add it.
- `CR-001` is in USD and its invoice `INV-2001` in EUR: the memo is subtracted in the plan's currency, after converting the invoice.
- Term length, amount per period and replacement dates are only in `notes`; there are no `end_date` or `superseded_by` fields. The replacement of `PLN-2001` comes from the `amends` field of `PLN-2001-A1`, not from parsing `notes`.
- There is no `customer_id`: customers are matched by exact `customer_name`.
- The JSON files keep their original formatting (leading indentation, one record per line in invoices and rates, pretty-printed plans and memos, trailing whitespace on the last line); the parser does not need it fixed.

Detector:

- Confidence is computed by re-running the plan's analysis with each alternative of each unset setting and comparing a signature (the periods without `no_data`, and the residue). `no_data` periods are left out: otherwise `auto_renewal` would lower the confidence as soon as the cutoff passes `data_through`, even though nobody knows whether there is a gap.
- `data_through` comes only from `data/` and from `issue_date`, never from the overlay: a make-good issued today in the sandbox would move it to today, and the whole gap up to `as_of` would turn from `no_data` into `missing` (D21).
- Memos follow their invoice: if the invoice goes to the residue (`unexplained`), so does its memo, with the same reason.
- Deterministic finding IDs, `{plan_id}:{type}:{period_start}`: recomputing replaces the finding instead of duplicating it. The `findings` reducer replaces all of the recomputed plan's findings, because a period can change type (`missing` → `underbilled` after a partial make-good).
- "Today" is injected (a clock or a date parameter), never `date.today()` inside the detector: tests use fixed dates. To tell whether the clock is the production one, compare with `==`: `clock is date.today` is always false (each access creates a new bound method).
- `validate_value` also compares the type: in Python `1 in [True, False]` is true.
- `ruff format` rewrites misplaced `# fmt: skip` comments: in the detector's multi-line `ResidueItem(...)` calls they go after the closing parenthesis.
- Tests with data the real app does not have run in pytest with fixtures (`DATA_DIR` points them there).
- The whole-month branch of `proration_ratio` is not reached from `data/` (no partial span of whole months), so it has its own unit test.

### Sandbox and actions

- A ledger record's status goes in `ledger_state` (D25), which does not clash with the data fields: `invoices` already has `status` (`paid` / `unpaid`).
- After writing to the sandbox, reload the data repository: otherwise the overlay (D6) and the recompute (D23) read the cached version.
- The evidence and the `reason` of every proposal are generated by the code from the detector finding, not by the LLM (D15).
- The sandbox is global and FastAPI runs sync routes in a threadpool: writes (ledger + audit log + ID counter) go under a process lock (`sandbox.LOCK`), and the `action_id` is computed inside it, after revalidating, so that a concurrent `apply` does not repeat an ID.
- On Windows, `os.replace` and `open` on a JSON file that another thread reads or replaces raise `PermissionError`: retry for a moment (`sandbox.retry_on_windows_lock`).
- The audit log entry keeps a copy of the written record: on rollback, its `ledger_state` has to be updated too.
- A sandbox amendment is rejected if its `effective_from` is earlier than the plan's current version: the later one has to be rolled back first.

### Agent and API

- Read the "LangGraph gotchas" of [agent.md](agent.md): reducers, one interrupt per pass, validate before interrupting, domain errors in the wrappers, synthetic `ToolMessage`, `update_state` with a pending interrupt, `SqliteSaver` with `check_same_thread=False` and the serializer allowlist, normalized content.
- A single function derives the events from `messages` (D28), used by `POST .../messages`, `POST .../decisions` and `GET /api/threads/{id}`: otherwise a reload shows something other than the turn did.
- The `tool_result` of an approved action arrives in the `decisions` response, not in the response to the message that asked for it; it is paired by `tool_call_id`.
- `ToolNode` runs the tool calls of one message in parallel and would number two proposals `PRP-001`: the `tools` node is custom and runs them in order; a message with reads and a guarded action runs the reads first and leaves the action for `human_review`.
- On resume, `human_review` runs again from the start (it revalidates before `interrupt()`, which returns the decision): if the data changed between the card and the approval, the proposal becomes `invalid` and nothing is written.
- `freshness_asked` is set when the detector reports the gap, before the agent asks: the state summary cannot say "you already asked" in that same turn (the LLM took it as a question already asked). `prompt._freshness_question_due_now` tells them apart.
- If the model fails, the checkpoint is left with `next=('agent',)`: a new message starts a clean run on the stored messages; it does not resume the failed one.
- A turn's events come from `messages[n_before:]`, not from subtracting event lists.
- `ToolMessage.artifact` carries the structured data (`findings`, `proposal`, `result`); `content` is what the LLM sees.
- `SqliteSaver.list` is a generator that holds the connection lock, and `get_state` takes the same (non-reentrant) lock: calling `get_state` inside the loop hangs the process (it was the `PUT /api/settings` hang). Collect the `thread_id`s first.
- Two SQLite connections initializing a new database at the same time fail with `database is locked` even with a `timeout`: `create_service()` without arguments is a singleton under a lock, with the checkpointer initialized on construction, WAL and a 30 s timeout.
- A `--reload` server started before the code existed keeps serving the old code: restart it before deciding that a 501 or a hang is a bug.

### Frontend

- An action's confirmation comes from its `tool_result` (paired by `tool_call_id`), not from the LLM's text.
- Totals and headers come from the API's `totals` and are not recomputed in the client; `no_data` periods do not count.
- Never interpolate objects into strings (you get `[object Object]`): format each field.
- `buildTurns` (`src/lib/turns.ts`) is a pure function of the full event list: a new turn on each `user_message`; `decisions` responses carry no `user_message` and fall into the turn that requested the approval. `tool_call_id`s are paired per turn (the mock restarts them per thread).
- An approval card is active only if `state.pending_approval.interrupt_id` matches its own; the "current" status of proposals and findings (status, stale) is read from `state`, not from the original event.
- `listPlans()` shares the in-flight request, and the refresh key is `''` while the thread loads: without that, StrictMode and the `state` change requested `/api/plans` three times.
- Vite listens on `localhost` (it can be `::1`): use `http://localhost:<port>`, not `127.0.0.1`.
- DOM tests: `// @vitest-environment jsdom` per file and an explicit `cleanup()` in `afterEach`.
- Desktop layout (`App.tsx`): `main` needs `min-w-0` and `aside` needs `shrink-0`. Without them a flex item's `min-width: auto` keeps `main` at the findings table's min-content width, the side panel shrinks from `w-96` to half and the page gets a horizontal scroll (the table's `overflow-x-auto` never kicks in).
- A hung `PUT /api/settings` shows no error in the UI (the button stays on "saving" with no limit): the client has no timeout.
- Playwright files are `*.e2e.ts`, not `*.spec.ts`: Vitest's default include also matches `.spec.ts` and would try to run them. They are type checked through `tsconfig.node.json`.
- In the e2e test, `getByText('PRP-001')` first matches the collapsed JSON of the trace (hidden): scope locators to a card's `data-testid`.
- The mock keeps the audit log in memory per process, so the e2e test expects `INV-MG-001` only on a fresh server: `reuseExistingServer: false`, `retries: 0` and its own ports (8100 / 5180).
