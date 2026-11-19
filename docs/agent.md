# Agent notes

Generic know-how for an LLM agent built with LangGraph. Read it before implementing the graph, the tools or the system prompt. Project decisions (`D#`) override anything here.

Paths are relative to `backend/`: `app/llm.py`
(the chat model, `get_llm()`), `tests/fake_llm.py` (`ScriptedLLM` for tests), `scripts/smoke_llm.py` (`make smoke-llm`).

## Principles

- **Deterministic code computes, the LLM interprets.** Amounts, matching, rules and validations live in plain domain functions with no LangGraph imports, tested without an LLM. The LLM picks tools, explains results and only reasons on its own about what the code cannot compute, clearly labeled as such.
- **State = messages + facts written only by tools.** Structured state (active entities, findings, proposals, decisions) is written by tools through `Command(update=...)`, never by the LLM's text. A short summary of it goes into the prompt every turn. History is memory, state is verifiable facts, side effects go through guarded actions.
- **Human approval in the graph, not in the tool.** Actions with side effects stop in a dedicated node with `interrupt()` before they run. Approval comes from the UI, carrying the interrupt id; a "yes" in the chat only makes the LLM call the action. The action also checks that an approval was recorded.
- **Retrieval by key, not by similarity,** when records have IDs, customers or dates. Embeddings bring back the most similar record, which can be the wrong one.
- **Inject the dependencies:** `build_graph(checkpointer, llm, ...)`. Tests use `MemorySaver` and `ScriptedLLM`; the app uses a persistent checkpointer, because `uvicorn --reload` loses an in-memory pending interrupt.

## LangGraph gotchas

- The fake chat models of LangChain do not implement `bind_tools`: use `ScriptedLLM`, which returns itself.
- `ToolNode` runs the tool calls of one message in parallel, and two writes to a channel without a reducer raise `InvalidUpdateError`. Give every state field a reducer (merge by id for dicts, last-wins for "active" fields, replace-by-scope for recomputed lists). Recomputed results need deterministic IDs (for example `{entity}:{type}:{key}`), so that recomputing replaces them instead of adding duplicates.
- Several `interrupt()` calls in one pass of a node share the same `Interrupt.id`. Guard one action per pass and self-loop while another is still undecided.
- Validate before interrupting: the action must be able to run (the target exists and is in the right status). Otherwise do not show an approval card.
- `ToolNode(handle_tool_errors=...)` only catches argument validation errors in LangGraph 1.x. Tool wrappers catch domain errors (`ValueError`, `LookupError`) and return `{"status": "error", "error": ...}` to the LLM.
- An exception inside a tool leaves an `AIMessage` with tool calls and no `ToolMessage`, and the provider rejects every later call. Before calling the model, add a synthetic `ToolMessage` for each unanswered call (only in what is sent, not in the state).
- `update_state` while an interrupt is pending reschedules the interrupted node and changes the interrupt id. Block state changes from the API while an action is pending (409).
- To create a thread from the API, use `update_state(..., as_node=<entry node>)` with the initial state. A thread exists only if its state has values; otherwise it is unknown (404), not empty.
- After a resume, the `ToolMessage` of the approved action comes back in the response to the decision, not in the turn that requested it. A UI that groups by turn pairs it with its tool call by `tool_call_id`.
- `SqliteSaver`: `check_same_thread=False` (FastAPI runs sync routes in a threadpool), and the serializer needs an allowlist of the Pydantic models in the state to load them back typed.
- An approved action that the domain then rejects goes back to pending, not to approved.
- With `ChatOpenAI`, `content` is a string; with other clients (`ChatAnthropic`) it can be a list of content blocks. Normalize before showing it.
- Prompt caching: the static system prompt in its own message with `cache_control` (OpenRouter forwards it to Anthropic), and the per-turn state summary in a second message after it.

## System prompt rules

Generic rules that held up in evals. Adapt the wording to the domain.

- Every fact and number comes from the tools. Never compute amounts or conversions in your head.
- Cite the evidence (record IDs, values, the calculation) and state the assumptions behind a conclusion.
- Propose only what the user asks for. A "yes" in the chat is not the approval: call the action and the system shows the approval card.
- Confirm an action only from the tool result, quoting the real ID it returned. If the user rejected it, say nothing was written and adapt.
- After an action, re-run the check before saying something is resolved.
- Anything the tools did not establish goes in a clearly labeled hypotheses section, citing IDs, never with a proposal.

## Testing

- Graph tests with `ScriptedLLM`, no network: interrupt, approve, reject, a guarded action mixed with reads in one message, and several guarded actions in a row.
- Tests that call the real LLM are marked `llm` (excluded from `make test`, run with `make eval-llm`). Structural asserts only (tools called, state, side effects), never on the text.
- `make smoke-llm` is a tool-calling round trip against the configured model. Run it as soon as `.env` has the key: it catches a wrong model ID or base URL before any agent code exists.
