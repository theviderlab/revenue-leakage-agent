"""Graph state (D2): messages plus structure that only tools and `human_review` write.

Every field has a reducer: tool calls of one message may produce several writes to a channel in
one step (agent.md gotchas). Values are plain JSON types so the checkpointer needs no serializer
allowlist for them.
"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_by_key(old: dict | None, new: dict | None) -> dict:
    """Merge dicts by key; a key in `new` replaces the same key in `old` (recomputed results)."""
    return {**(old or {}), **(new or {})}


def last_wins(old: Any, new: Any) -> Any:
    return new


def sticky_true(old: bool | None, new: bool | None) -> bool:
    return bool(old) or bool(new)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    active_plan_id: Annotated[str | None, last_wins]
    active_customer: Annotated[str | None, last_wins]
    # plan_id -> PlanFindings dict (contract shape) with the settings_version it was computed with
    findings: Annotated[dict[str, dict], merge_by_key]
    # proposal_id -> Proposal dict (contract shape, with `status`)
    proposals: Annotated[dict[str, dict], merge_by_key]
    # tool_call_id -> {decision, interrupt_id, action, affected_plans, ...}
    reviews: Annotated[dict[str, dict], merge_by_key]
    freshness_asked: Annotated[bool, sticky_true]


def initial_state() -> AgentState:
    return {
        "messages": [],
        "active_plan_id": None,
        "active_customer": None,
        "findings": {},
        "proposals": {},
        "reviews": {},
        "freshness_asked": False,
    }
