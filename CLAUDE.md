# Revenue Leakage Agent

## Project

Revenue Leakage Agent: a stateful LangGraph chat agent that investigates billing anomalies between billing plans and invoices, proposes make-good invoices, credit memos and plan amendments, and applies them to a JSON sandbox only after the user approves each one in the chat.

## Structure

```
backend/             FastAPI app (uv project, Python 3.12)
  app/               application code: config.py (settings), main.py (FastAPI app, /api router)
  app/llm.py         chat model factory (get_llm)
  scripts/           smoke_llm.py (make smoke-llm)
  tests/             pytest tests
frontend/            React SPA (Vite + React + TypeScript, Tailwind v4, Vitest)
  src/               application code
  src/lib/           API client (api.ts) and generated API types (api-types.ts)
docs/                design decisions (see Docs)
.github/workflows/   CI (runs make install + make check)
```

## Commands

- `make install` - install backend and frontend dependencies
- `make dev` - run backend (port 8000) and frontend (port 5173) together
- `make dev-backend` / `make dev-frontend` - run one side only
- `make test` - backend tests (pytest) and frontend tests (Vitest)
- `make lint` - ruff check, ruff format --check, tsc -b, oxlint
- `make format` - ruff format + ruff check --fix
- `make check` - lint + test
- `make e2e` - Playwright smoke test against the mock (own ports 8100 / 5180; `make e2e-install` once for Chromium); a separate CI job, not part of `check`
- `make types` - generate `frontend/src/lib/api-types.ts` from the backend OpenAPI schema
- `make reset` - delete the runtime state (`STATE_DIR`, default `.state/`); stop the app first
- `make smoke-llm` - tool-calling round trip against the real LLM (needs `LLM_API_KEY` in `.env`)
- `make eval-llm` - tests marked `llm`, against the real LLM
- `make clean` - remove caches and build output

## Conventions

- Run `make check` before calling a task done.
- Add dependencies with `uv add` (in `backend/`) or `npm install` (in `frontend/`); never edit manifests by hand.
- API routes live under `/api`.
- The Vite dev server proxies `/api` to the backend, so dev and production use the same URLs and no CORS is needed.
- No emojis in logs or console output (they break the Windows cp1252 console).
- Config comes from the root `.env` (see `.env.example`).
- Runtime state (SQLite, local ledgers, caches the app writes) goes under `settings.state_dir` (`STATE_DIR`, default `.state/`, git-ignored), never at hard-coded paths. Tests and a second dev instance point it elsewhere. `make reset` deletes it (stop the app first).
- Tests are hermetic: fixed dates, and no writes outside a temp dir.
- When the expected results are known, write them as a table in `docs/` and test against that table.

## Python

- Open text files with an explicit `encoding="utf-8"`: the Windows default is cp1252 and breaks on non-ASCII data.
- Write files atomically: write a temp file in the same folder, then `os.replace` it over the target.
- SQLite from FastAPI needs `check_same_thread=False`, because sync routes run in a threadpool. In WAL mode it also creates `-wal` and `-shm` files, so `.gitignore` uses `*.db*`.

## Frontend

- Tailwind v4: the `@tailwindcss/vite` plugin plus `@import "tailwindcss"`. There is no `tailwind.config.js`, no PostCSS config and no `@tailwind` directives. For rendered markdown, `@tailwindcss/typography` (`prose`).
- No component library and no state manager: `useState` / `useReducer` + `fetch`.
- Totals, counts and statuses come from what the API computes; the client never recomputes them.
- Never interpolate an object or an unknown value into a string (`[object Object]`): format each field explicitly.
- Text written by the LLM is markdown: render it with `src/components/Markdown.tsx` (react-markdown + remark-gfm), never as raw text.
- Streaming from a POST: `fetch` + `ReadableStream`, not `EventSource` (it only does GET).
- API types come from `make types` into `src/lib/api-types.ts` and are never written by hand.

## Agent

- The chat model comes from `get_llm()` in `app/llm.py` (OpenAI-compatible: `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`). Nothing else knows the provider.
- Read `docs/agent.md` before implementing the graph, the tools or the system prompt: principles, LangGraph gotchas and prompt rules.
- Graph tests use `ScriptedLLM` from `tests/fake_llm.py`, never the network. Tests that call the real LLM are marked `llm` and assert structure, never text.

## Local runs

- Run a second instance with its own ports and state: `make dev BACKEND_PORT=8001 FRONTEND_PORT=5174 STATE_DIR=.state-dev2 AGENT_BACKEND=mock` (the command line wins over `.env`). Stop what you start when you finish.
- The API contract goes first: Pydantic models in `backend/app/schemas.py` and the endpoints and flows in `docs/api.md`. `src/lib/api-types.ts` is only generated, by `make types`.

## Domain

Revenue leakage: money a contract says should be billed but was not (or was billed wrong). The agent compares what each billing plan expects per period with what was invoiced, net of credit memos, in the plan's currency.

- **Data** (`data/`, read-only): `billing_plans.json` (contracts; `amends` links an amendment to the plan it replaces), `invoices.json`, `credit_memos.json`, `exchange_rates.json`. Joined by `plan_id` / `invoice_id`; customers only by `customer_name`.
- **Corrective actions**: make-good invoice (recover missed or underbilled revenue), credit memo (reduce an overbilled invoice), plan amendment (change the contract itself), plus linking or dismissing an invoice with no plan.
- **Sandbox** (under `settings.state_dir`): append-only ledgers with the schema of `data/` plus `audit_log.json`; rollback marks, never deletes. Nothing is written without an approval card.
- **Detector**: deterministic `detect_anomalies` (no LLM) returns each expected period with a status (`ok`, `missing`, `underbilled`, `overbilled`, `pending_decision`, `no_data`), totals, a per-plan confidence and an `unexplained` residue. The LLM only explains and labels hypotheses.
- **Analysis settings** (not the app config): cutoff `as_of`, `data_up_to_date` and the assumptions the data does not decide (`period_calendar`, `amendment_proration`, `auto_renewal`); changed only in the UI panel. Fixed rules come from D20 (billing in advance) and from what the data decides.

Where it is documented: `docs/data-analysis.md` (the data, what matches and the ambiguities A1 to A15), `docs/design.md` (decisions D19 to D23 on the domain rules, the detector ground truth table the tests use, and the system prompt rules).

## Docs

The design lives in `docs/` and is the single source of truth. Anything needed to understand or change the code (decisions, gotchas, rules) is written in `docs/` before the code, never left only in a conversation.

- `docs/design.md` - stack, architecture decisions (`D#`) with their rejected alternatives, detector ground truth, system prompt rules and implementation gotchas per area.
- `docs/agent.md` - generic know-how for the LLM agent (principles, LangGraph gotchas, prompt rules); project `D#` override it.
- `docs/api.md` - the API contract: endpoints, events, flows and backend details.
- `docs/data-analysis.md` - analysis of the data in `data/` and its ambiguities.

Workflow (autonomous): the decisions in `docs/` and the ones the user dictates are closed. Plan and implement without asking for confirmation. When a decision is not obvious, take it with a recommendation, record it as a `D#` in `design.md` (with the losing alternatives under its "Rejected" list) and keep going. Ask only when a request is ambiguous or contradicts an existing `D#`, or when the action cannot be undone. List every decision you took on your own in the final summary.

Before changing an area, read its `D#` and its gotchas in `design.md`. Rejected alternatives are not re-proposed without a new reason. A change is done when `make check` is green.

Gotchas: an implementation detail with no real alternative (a library quirk, a data trap, a platform issue) is not a `D#`. It goes to the Gotchas section of `design.md`, under its area. When you hit a bug that was not obvious, add it there.

Dictation mode: when the user dictates decisions that are already closed (for example "decision · why · rejected" lines), record each one directly as a `D#` in `design.md`, with the rejected alternatives under it. Implementation details in a dictation go to Gotchas, not `D#`. Do not discuss or re-recommend; only ask if a line is ambiguous or contradicts an existing `D#`. Do not implement anything unless asked.

Consistency review: when asked, review `design.md` end to end without implementing anything: contradictions between `D#`, missing decisions, changes that did not reach every affected `D#`, and broken references. Also list the gaps you filled yourself while recording, and with what criterion. Report the list, fix only what is obvious, and ask about the rest.
