# API

The API contract. The models live in `backend/app/schemas.py`; the frontend types are generated with `make types` into `frontend/src/lib/api-types.ts` and are never written by hand. The decisions are in [design.md](design.md) (D9, D10, D14, D24, D25, D28, D34).

`AGENT_BACKEND` (`mock` / `real`, in `app/config.py`) picks the implementation of the `AgentService` interface (`app/service.py`); the routes do not know which one runs. `mock` is in `app/mock.py`; `real` is `RealAgentService` (`app/agent/service.py`, `create_service()`, one singleton per process).

Amounts: numbers with two decimals, already rounded by whoever produces them (D31); the client only formats them. Dates: `YYYY-MM-DD`.

## Status codes

| Code | When | Body |
|---|---|---|
| 200 | Success. Agent errors (LLM down, invalid response) are also 200, with an `error` event | |
| 404 | The thread does not exist | `{"detail": "..."}` |
| 409 | The state does not allow it (see each endpoint) | `{"detail": "..."}` |
| 422 | Invalid body, or `as_of` later than today | FastAPI format |

## Endpoints

| Method and path | Body | Response | Errors |
|---|---|---|---|
| `GET /api/health` | | `{status, agent_backend}` (`mock` / `real`: which implementation runs, D35) | |
| `GET /api/plans` | | `Plan[]` (D6, D12) | |
| `GET /api/settings` | | `SettingsResponse`: `version`, `data_through`, `settings[]` with `key`, `value` (`null` = not set), `default`, `valid_values` | |
| `PUT /api/settings` | `SettingsUpdate`: only the fields present change; `null` goes back to the default | `SettingsResponse`, with `version` incremented if anything changed | 409 if any thread has a pending action; 422 |
| `POST /api/threads` | | `{thread_id, events: [], state}` | |
| `GET /api/threads/{id}` | | `{events, state}` of the whole history | 404 |
| `POST /api/threads/{id}/messages` | `{text}` (not empty) | `{events, state}` of the turn | 404; 409 if the thread has a pending action |
| `POST /api/threads/{id}/decisions` | `{interrupt_id, decision: "approve" \| "reject"}` | `{events, state}` of the decision | 404; 409 if there is no pending action or the `interrupt_id` does not match |
| `GET /api/graph` | | Mermaid text (`text/plain`) | |

## Events (D28)

A union discriminated by `type`. Each response carries the new events, in order; `GET /api/threads/{id}` carries all of them.

| `type` | Fields |
|---|---|
| `user_message`, `assistant_message` | `text` (markdown in the second) |
| `tool_call` | `tool_call_id`, `name`, `args` |
| `tool_result` | `tool_call_id`, `name`, `status` (`ok` / `error` / `rejected_by_user`), `result` |
| `findings` | `tool_call_id`, `findings`: `plans[]` (`confidence`, `confidence_reasons`, `periods`, `totals`, `settings_version`, `stale`), `totals`, `assumptions`, `unexplained` |
| `proposal` | `tool_call_id`, `proposal` |
| `approval_request` | `tool_call_id`, `interrupt_id`, `action` (`PendingAction`) |
| `error` | `message` |

`findings` and `proposal` come with their tool's `tool_result` (same `tool_call_id`).

## Pending action

A single shape for `apply` and `rollback`: `PendingAction` `{kind: "apply" | "rollback", proposal, action_id, summary, evidence}`. For `apply`, `proposal` carries the proposal and `action_id` is `null`; for `rollback`, `action_id` is the action to roll back and `proposal` is `null`. The same `PendingAction` goes in the `approval_request` event and in `state.pending_approval` (`{interrupt_id, action}`, or `null`). There is at most one per thread.

## State (`ThreadState`)

`active_plan_id`, `active_customer`, `findings` (`PlanFindings` with `stale`), `proposals` (lifecycle of D34), `pending_approval`, `actions` (global audit log, D25: `action_id`, `type`, `proposal_id`, `thread_id`, `plan_id`, `payload`, `evidence`, `reason`, `applied_at`, `rolled_back_at`) and `settings_version`.

## Flows

The action's `tool_call_id` is the same in the `approval_request` and in the later `tool_result`.

- **Message:** `POST messages` -> `user_message`, tool calls and results, `findings` / `proposal` when they apply, `assistant_message`. If the agent asks for `apply` or `rollback`, the turn ends in an `approval_request` and `state.pending_approval` is set.
- **Approve:** `POST decisions` with `approve` -> `tool_result` `ok` with the real `action_id`, the recompute of the affected plan (D23: `tool_call`, `tool_result`, `findings`) and `assistant_message`. `state.actions` includes the new action (or `rolled_back_at` set, for a rollback).
- **Reject:** `reject` -> `tool_result` with `status: "rejected_by_user"` and `assistant_message`; nothing is written and the proposal becomes `rejected`.
- **Rollback:** like apply, but only for the last active action (D8). Any other action comes back as an `error` `tool_result` that names the last one, with no card.
- **Reload:** `GET threads/{id}` returns all the events and the state; the frontend renders them the same way as during the turn. A 404 means creating a new thread (D18).
- **409:** an old, repeated card, or one with no pending action, approves nothing; the state does not change.

## Real backend details (D36)

- `GET /api/settings`: `data_up_to_date` shows `default: null` (D21: `null` until it is set), although the detector treats it as `false`. A `PUT` with no real changes does not bump `version`.
- The `tool_result.result` of `detect_anomalies` is a summary (`scope`, `totals`, `plans[]`, `unexplained`); the detail goes in the `findings` event. For the `propose_*` tools it is the proposal.
- The D23 recompute is a synthetic `tool_call` / `tool_result` pair for `detect_anomalies` (`tool_call_id` `recheck-<call_id>-<plan>`) plus its `findings`, after the `tool_result` of the `apply` or `rollback`; only once, after the last guarded action of the message.
- `approval_request` is emitted again by `GET threads/{id}` for cards already decided, the same as during the turn. If a message carries two guarded actions, the second card arrives at the end of the response to the first decision.
- `error` events live in the service's memory: a reload after restarting the process does not show them.
- `state.proposals`: `invalid` if the revalidation fails (including the one that runs on resume after approving); `pending` only if the domain rejects it after the approval was given.
- `state` does not expose `freshness_asked` (D35).

## Mock (D29)

A keyword script, with no LLM and no detector, using the amounts of `PLN-1001` (Base, `as_of = 2026-06-30`). Within a thread:

| Message contains | Effect |
|---|---|
| `check`, `investigate`, `leak`, `PLN-1001` | `detect_anomalies` + `findings`: 49,000 expected, 43,500 net, -5,500; February 2026 is missing; May is `overbilled` by 1,500; `INV-1003` and `INV-4001` in the residue |
| `make-good`, `propose` (+ a month in English, Spanish or `2026-MM`; February by default) | `propose_make_good_invoice` -> `proposal` `PRP-00N` for 7,000. An error as a `tool_result` if the month is not missing or the plan is another one |
| `apply`, `yes`, `aplicar` | `apply` -> `approval_request` for the last `pending` proposal |
| `rollback`, `roll it back`, `undo` | `rollback` -> `approval_request` for the last active action; if it names another `INV-MG-N` (for example `INV-MG-099`), an `error` `tool_result` |
| `currency` | answers USD if there is an active plan |
| `error`, `fail` | `error` event with 200 |
| anything else | help with the script |

Applying creates `INV-MG-001`, `INV-MG-002`, ... (sequential, never reused); the recompute shows 2026-02 as `ok` and the total at +1,500 (May is still `overbilled`), and the rollback brings it back to -5,500. `GET /api/plans` returns the four plans of `data/`; `GET /api/settings` starts with every setting unset and `PUT` changes them in memory. The mock's state lives in memory and is lost on restart.
