"""One function derives the API events from the graph state (D28, gotchas: agent and API).

POST messages, POST decisions and GET threads all use it, so a reload shows what the turn showed.
Events come from `messages`; the approval card of a pending action comes from the interrupt, and
from `reviews` once it was decided.
"""

import json
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from app.agent.tools import GUARDED
from app.schemas import (
    ApprovalRequestEvent,
    AssistantMessageEvent,
    ErrorEvent,
    Event,
    FindingsEvent,
    FindingsPayload,
    PendingAction,
    Proposal,
    ProposalEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)


def text_of(content: Any) -> str:
    """Normalize message content: a string, or a list of blocks (other chat clients)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content or "")


def _parse(content: Any) -> Any:
    try:
        return json.loads(text_of(content))
    except ValueError:
        return {"message": text_of(content)}


def _status(result: Any) -> str:
    if isinstance(result, dict):
        s = result.get("status")
        if s == "error":
            return "error"
        if s == "rejected_by_user":
            return "rejected_by_user"
    return "ok"


def derive_events(
    messages: list[AnyMessage],
    *,
    reviews: dict[str, dict],
    pending: dict | None,
    start: int = 0,
    errors: list[tuple[int, str]] | None = None,
) -> list[Event]:
    """Events of `messages[start:]`. `pending` is the interrupt payload, if one is waiting.

    `errors` are (message_count_when_it_happened, text) pairs, placed where they occurred.
    """
    events: list[Event] = []
    errors = errors or []

    def add_errors(upto: int) -> None:
        for at, msg in errors:
            if at == upto:
                events.append(ErrorEvent(type="error", message=msg))

    for i in range(start, len(messages)):
        add_errors(i)
        m = messages[i]
        if isinstance(m, HumanMessage):
            events.append(UserMessageEvent(type="user_message", text=text_of(m.content)))
        elif isinstance(m, AIMessage):
            text = text_of(m.content).strip()
            if text:
                events.append(AssistantMessageEvent(type="assistant_message", text=text))
            for c in m.tool_calls:
                events.append(
                    ToolCallEvent(
                        type="tool_call", tool_call_id=c["id"], name=c["name"], args=c["args"]
                    )
                )
                if c["name"] in GUARDED:
                    card = _approval(c["id"], reviews, pending)
                    if card:
                        events.append(card)
        elif isinstance(m, ToolMessage):
            result = _parse(m.content)
            art = m.artifact if isinstance(m.artifact, dict) else {}
            events.append(
                ToolResultEvent(
                    type="tool_result",
                    tool_call_id=m.tool_call_id,
                    name=m.name or "",
                    status=_status(result),
                    result=art.get("result", result),
                )
            )
            if art.get("findings"):
                events.append(
                    FindingsEvent(
                        type="findings",
                        tool_call_id=m.tool_call_id,
                        findings=FindingsPayload.model_validate(art["findings"]),
                    )
                )
            if art.get("proposal"):
                events.append(
                    ProposalEvent(
                        type="proposal",
                        tool_call_id=m.tool_call_id,
                        proposal=Proposal.model_validate(art["proposal"]),
                    )
                )
    add_errors(len(messages))
    return events


def _approval(call_id: str, reviews: dict[str, dict], pending: dict | None) -> Event | None:
    if pending and pending.get("tool_call_id") == call_id:
        return ApprovalRequestEvent(
            type="approval_request",
            tool_call_id=call_id,
            interrupt_id=pending["interrupt_id"],
            action=PendingAction.model_validate(pending["action"]),
        )
    review = reviews.get(call_id)
    if review and review.get("interrupt_id"):
        return ApprovalRequestEvent(
            type="approval_request",
            tool_call_id=call_id,
            interrupt_id=review["interrupt_id"],
            action=PendingAction.model_validate(review["action"]),
        )
    return None
