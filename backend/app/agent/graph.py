"""The agent graph (D1, D3, D23, D34): `agent` -> `tools` -> `human_review`, no router.

- `agent`: the LLM with the tools. It picks tools; nothing classifies the user's intent.
- `tools`: runs the non-guarded tool calls of the last message, one at a time.
- `human_review`: the only node that stops the graph. One guarded call (`apply` / `rollback`) per
  pass: validate (no card if it cannot run), `interrupt()` for the user's decision, then run it.
  After the last guarded call of a message it re-checks the affected plans (D23).
"""

import json
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.prompt import state_summary, static_message
from app.agent.state import AgentState
from app.agent.tools import GUARDED, Ctx, Outcome, error, llm_tools, run_tool
from app.domain.billing import Billing
from app.domain.errors import DomainError, NotFoundError


def unanswered_calls(messages: list[AnyMessage]) -> list[dict]:
    """Tool calls of the last AI message that have no ToolMessage yet."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, AIMessage) and m.tool_calls:
            answered = {t.tool_call_id for t in messages[i + 1 :] if isinstance(t, ToolMessage)}
            return [c for c in m.tool_calls if c["id"] not in answered]
        if isinstance(m, AIMessage):
            return []
    return []


def with_answers_for_orphans(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Add a synthetic ToolMessage for every unanswered call, only in what is sent to the model.

    An exception inside a step can leave an AI message with tool calls and no results; the provider
    rejects every later call otherwise (agent.md gotchas).
    """
    out: list[AnyMessage] = []
    for i, m in enumerate(messages):
        out.append(m)
        if isinstance(m, AIMessage) and m.tool_calls:
            answered = set()
            for t in messages[i + 1 :]:
                if isinstance(t, ToolMessage):
                    answered.add(t.tool_call_id)
                elif isinstance(t, AIMessage):
                    break
            for c in m.tool_calls:
                if c["id"] not in answered:
                    out.append(
                        ToolMessage(
                            content=json.dumps(
                                {"status": "error", "error": "this call did not run; try again"}
                            ),
                            tool_call_id=c["id"],
                            name=c["name"],
                            status="error",
                        )
                    )
    return out


def tool_message(call: dict, outcome: Outcome) -> ToolMessage:
    return ToolMessage(
        content=outcome.text(),
        tool_call_id=call["id"],
        name=call["name"],
        status="error" if outcome.status == "error" else "success",
        artifact=outcome.artifact or None,
    )


def build_graph(
    checkpointer: Any, llm: BaseChatModel | Callable[[], BaseChatModel], billing: Billing
):
    """`llm` may be a factory: it is called on the first model call, so a missing API key is an
    error event in the chat instead of a failure to start the app (D9)."""
    cache: dict[str, Any] = {}

    def bound() -> Any:
        if "llm" not in cache:
            model = llm if isinstance(llm, BaseChatModel) else llm()
            cache["llm"] = model.bind_tools(llm_tools())
        return cache["llm"]

    def thread_of(config: RunnableConfig) -> str:
        return config["configurable"]["thread_id"]

    def agent(state: AgentState) -> dict:
        settings = billing.settings()
        summary = state_summary(
            dict(state),
            settings=settings,
            today=billing.today().isoformat(),
            data_through=billing.describe_settings()["data_through"],
            actions=billing.list_actions(),
        )
        messages = [static_message(), summary, *with_answers_for_orphans(state["messages"])]
        return {"messages": [bound().invoke(messages)]}

    def route_after_agent(state: AgentState) -> str:
        return "tools" if unanswered_calls(state.get("messages", [])) else END

    def tools(state: AgentState, config: RunnableConfig) -> dict:
        calls = [c for c in unanswered_calls(state["messages"]) if c["name"] not in GUARDED]
        ctx = Ctx(
            billing=billing,
            thread_id=thread_of(config),
            proposals=dict(state.get("proposals") or {}),
            freshness_asked=bool(state.get("freshness_asked")),
        )
        out: list[ToolMessage] = []
        for call in calls:
            out.append(tool_message(call, run_tool(ctx, call["name"], call["args"])))
        return {"messages": out, **ctx.update}

    def route_after_tools(state: AgentState) -> str:
        return "human_review" if unanswered_calls(state["messages"]) else "agent"

    def route_after_review(state: AgentState) -> str:
        return "human_review" if unanswered_calls(state["messages"]) else "agent"

    def human_review(state: AgentState, config: RunnableConfig) -> dict:
        call = next(c for c in unanswered_calls(state["messages"]) if c["name"] in GUARDED)
        proposals = dict(state.get("proposals") or {})
        update: dict[str, Any] = {}

        def reply(outcome: Outcome) -> dict:
            return {"messages": [tool_message(call, outcome)], **update}

        # 1. Validate: if the action cannot run there is no card, the tool returns the error.
        try:
            if call["name"] == "apply":
                pid = str(call["args"].get("proposal_id", ""))
                proposal = proposals.get(pid)
                if proposal is None:
                    return reply(
                        error(f"unknown proposal '{pid}'; pending: {_pending_ids(proposals)}")
                    )
                if proposal["status"] != "pending":
                    return reply(
                        error(
                            f"proposal {pid} is {proposal['status']}; only a pending one "
                            "can be applied"
                        )
                    )
                try:
                    fresh = billing.validate_proposal(proposal)
                except DomainError as e:
                    update["proposals"] = {pid: {**proposal, "status": "invalid"}}
                    return reply(error(f"proposal {pid} no longer holds: {e}"))
                action = {
                    "kind": "apply",
                    "proposal": proposal,
                    "action_id": None,
                    "summary": f"Apply {pid}: {proposal['reason']}",
                    "evidence": fresh.evidence,
                }
                affected = fresh.affected_plans
            else:
                action_id = str(call["args"].get("action_id", ""))
                entry = billing.check_rollback(action_id)
                action = {
                    "kind": "rollback",
                    "proposal": None,
                    "action_id": action_id,
                    "summary": f"Roll back {action_id}: {entry['reason']}",
                    "evidence": entry["evidence"],
                }
                affected = billing.affected_plans(entry)
        except (DomainError, NotFoundError) as e:
            return reply(error(str(e)))

        # 2. Stop for the user. Resumed with {"decision": "approve" | "reject"}.
        resume = interrupt({"tool_call_id": call["id"], "action": action})
        decision = resume.get("decision") if isinstance(resume, dict) else resume
        interrupt_id = resume.get("interrupt_id") if isinstance(resume, dict) else None
        review = {"decision": decision, "action": action, "interrupt_id": interrupt_id,
                  "affected_plans": affected}  # fmt: skip

        if decision != "approve":
            update["reviews"] = {call["id"]: review}
            if call["name"] == "apply":
                update["proposals"] = {pid: {**proposal, "status": "rejected"}}
            return reply(
                Outcome(
                    {
                        "status": "rejected_by_user",
                        "message": "The user rejected it; nothing was written.",
                    },
                    "ok",
                )
            )

        # 3. Approved: the approval is recorded before anything is written.
        update["reviews"] = {call["id"]: review}
        try:
            if call["name"] == "apply":
                entry = billing.apply(proposal, proposal_id=pid, thread_id=thread_of(config))
                update["proposals"] = {pid: {**proposal, "status": "applied"}}
                result = {"status": "applied", "action_id": entry["action_id"],
                          "type": entry["type"],
                          "plan_id": entry["plan_id"], "proposal_id": pid}  # fmt: skip
            else:
                entry = billing.rollback(action_id)
                result = {"status": "rolled_back", "action_id": entry["action_id"],
                          "type": entry["type"], "plan_id": entry["plan_id"]}  # fmt: skip
        except (DomainError, NotFoundError) as e:
            # The domain rejected an approved action: it goes back to pending (D34).
            review["decision"] = "approved_but_failed"
            return reply(
                error(f"approved, but the domain rejected it and nothing was written: {e}")
            )

        messages: list[Any] = [tool_message(call, Outcome(result))]
        remaining = [c for c in unanswered_calls(state["messages"]) if c["id"] != call["id"]]
        if not any(c["name"] in GUARDED for c in remaining):
            messages += _recheck(state, config, review, call, update)
        return {"messages": messages, **update}

    def _recheck(state: AgentState, config: RunnableConfig, review: dict, call: dict, update: dict):
        """D23: recompute only the affected plans, and only if no finding is stale."""
        affected: list[str] = []
        reviews = {**(state.get("reviews") or {}), call["id"]: review}
        last_ai = next(
            m for m in reversed(state["messages"]) if isinstance(m, AIMessage) and m.tool_calls
        )
        for c in last_ai.tool_calls:
            r = reviews.get(c["id"])
            if r and r.get("decision") == "approve":
                affected += [p for p in r.get("affected_plans", []) if p not in affected]
        version = billing.settings().version
        stale = any(
            f["settings_version"] != version for f in (state.get("findings") or {}).values()
        )
        if not affected or stale:
            return []
        calls = [
            {
                "name": "detect_anomalies",
                "args": {"plan_id": pid},
                "id": f"recheck-{call['id']}-{pid}",
                "type": "tool_call",
            }  # fmt: skip
            for pid in affected
        ]
        out: list[Any] = [AIMessage(content="", tool_calls=calls)]
        ctx = Ctx(billing, thread_of(config), {}, bool(state.get("freshness_asked")))
        for c in calls:
            out.append(tool_message(c, run_tool(ctx, c["name"], c["args"])))
        update["findings"] = ctx.update.get("findings", {})
        if "freshness_asked" in ctx.update:
            update["freshness_asked"] = True
        return out

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent)
    builder.add_node("tools", tools)
    builder.add_node("human_review", human_review)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, ["tools", END])
    builder.add_conditional_edges("tools", route_after_tools, ["human_review", "agent"])
    builder.add_conditional_edges("human_review", route_after_review, ["human_review", "agent"])
    return builder.compile(checkpointer=checkpointer)


def _pending_ids(proposals: dict[str, dict]) -> str:
    ids = [p["proposal_id"] for p in proposals.values() if p["status"] == "pending"]
    return ", ".join(ids) or "none"
