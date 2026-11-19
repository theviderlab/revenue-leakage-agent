"""Real implementation of `AgentService` (D9, D10, D14): the graph behind the /api routes.

Threads are created by the server; a thread exists only if its state has values. Every error of
the agent (LLM down, invalid response) is an `error` event with HTTP 200, not a 500 (D9).
"""

import sqlite3
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.agent.events import derive_events, text_of
from app.agent.graph import build_graph
from app.agent.state import initial_state
from app.domain.billing import Billing
from app.domain.errors import DomainError
from app.schemas import (
    ApprovalRequestEvent,
    AuditAction,
    CreateThreadResponse,
    Event,
    PendingAction,
    PendingApproval,
    Plan,
    PlanFindings,
    Proposal,
    SettingInfo,
    SettingsResponse,
    SettingsUpdate,
    ThreadState,
    TurnResponse,
)
from app.service import Conflict, InvalidSettings, ThreadNotFound

RECURSION_LIMIT = 40
# data_up_to_date is "not set" until the user answers (D21), so the panel shows no default.
DEFAULT_SHOWN = {"data_up_to_date": None}


class RealAgentService:
    def __init__(
        self,
        billing: Billing,
        llm: BaseChatModel | Callable[[], BaseChatModel],
        checkpointer: Any,
    ):
        self.billing = billing
        self.checkpointer = checkpointer
        self.compiled = build_graph(checkpointer, llm, billing)
        self._locks: dict[str, threading.Lock] = {}
        self._master = threading.Lock()
        # (message count when it happened, text), per thread: errors are events, not messages
        self._errors: dict[str, list[tuple[int, str]]] = {}

    # --- plans and settings ---------------------------------------------------------------

    def list_plans(self) -> list[Plan]:
        snap = self.billing.snapshot()
        out = []
        for p in snap.plans.values():
            v = p.latest
            out.append(
                Plan(
                    plan_id=p.plan_id,
                    customer_name=p.customer_name,
                    total_value=float(v.total_value),
                    currency=p.currency,
                    cadence=v.cadence,
                    start_date=p.start_date,
                    amends=p.amends,
                    entitlements=list(v.entitlements),
                    notes=v.notes,
                    source="sandbox" if any(x.source == "sandbox" for x in p.versions) else "data",
                )
            )
        return out

    def get_settings(self) -> SettingsResponse:
        d = self.billing.describe_settings()
        infos = []
        for key, s in d["settings"].items():
            valid = s["valid"]
            infos.append(
                SettingInfo(
                    key=key,
                    value=s["value"],
                    default=DEFAULT_SHOWN.get(key, s["default"]),
                    valid_values=list(valid) if isinstance(valid, list) else None,
                )
            )
        return SettingsResponse(
            version=d["version"], data_through=date.fromisoformat(d["data_through"]), settings=infos
        )

    def update_settings(self, update: SettingsUpdate) -> SettingsResponse:
        if self._any_pending():
            raise Conflict("There is a pending action; decide it before changing settings")
        changes = {
            k: (v.isoformat() if isinstance(v, date) else v)
            for k in update.model_fields_set
            for v in [getattr(update, k)]
        }
        current = self.billing.settings().values
        if any(current.get(k) != v for k, v in changes.items()):
            try:
                self.billing.update_settings(changes)
            except DomainError as e:
                raise InvalidSettings(str(e)) from e
        return self.get_settings()

    # --- threads --------------------------------------------------------------------------

    def create_thread(self) -> CreateThreadResponse:
        thread_id = f"thr-{uuid4().hex[:8]}"
        self.compiled.update_state(self._config(thread_id), initial_state(), as_node="agent")
        return CreateThreadResponse(
            thread_id=thread_id, events=[], state=self._state(self._snapshot(thread_id))
        )

    def get_thread(self, thread_id: str) -> TurnResponse:
        snap = self._snapshot(thread_id)
        return TurnResponse(events=self._events(thread_id, snap, start=0), state=self._state(snap))

    def send_message(self, thread_id: str, text: str) -> TurnResponse:
        with self._lock(thread_id):
            snap = self._snapshot(thread_id)
            if self._pending(snap):
                raise Conflict("There is a pending action; approve or reject it first")
            before = len(snap.values["messages"])
            errors_before = len(self._errors.get(thread_id, []))
            self._run(thread_id, {"messages": [HumanMessage(text)]})
            return self._turn(thread_id, before, errors_before)

    def decide(
        self, thread_id: str, interrupt_id: str, decision: Literal["approve", "reject"]
    ) -> TurnResponse:
        with self._lock(thread_id):
            snap = self._snapshot(thread_id)
            pending = self._pending(snap)
            if pending is None or pending["interrupt_id"] != interrupt_id:
                raise Conflict("interrupt_id does not match the pending action")
            before = len(snap.values["messages"])
            errors_before = len(self._errors.get(thread_id, []))
            self._run(
                thread_id, Command(resume={"decision": decision, "interrupt_id": interrupt_id})
            )
            return self._turn(thread_id, before, errors_before)

    def graph(self) -> str:
        return self.compiled.get_graph().draw_mermaid()

    # --- internals ------------------------------------------------------------------------

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}

    def _lock(self, thread_id: str) -> threading.Lock:
        with self._master:
            return self._locks.setdefault(thread_id, threading.Lock())

    def _snapshot(self, thread_id: str):
        snap = self.compiled.get_state(self._config(thread_id))
        if not snap.values:
            raise ThreadNotFound(thread_id)
        return snap

    @staticmethod
    def _pending(snap) -> dict | None:
        for task in snap.tasks:
            for it in task.interrupts:
                v = it.value
                return {
                    "interrupt_id": it.id,
                    "tool_call_id": v["tool_call_id"],
                    "action": v["action"],
                }
        return None

    def _any_pending(self) -> bool:
        # Collect the thread ids first: `list` is a generator that holds the SQLite connection
        # lock while it iterates, and `get_state` needs the same (non-reentrant) lock.
        thread_ids = {cp.config["configurable"]["thread_id"] for cp in self.checkpointer.list(None)}
        return any(self._pending(self.compiled.get_state(self._config(t))) for t in thread_ids)

    def _run(self, thread_id: str, payload: Any) -> None:
        try:
            self.compiled.invoke(payload, self._config(thread_id))
        except Exception as e:  # LLM down, invalid response, recursion limit: an event, not a 500
            n = len(self.compiled.get_state(self._config(thread_id)).values.get("messages", []))
            self._errors.setdefault(thread_id, []).append((n, _error_text(e)))

    def _events(self, thread_id: str, snap, start: int, errors_from: int = 0) -> list[Event]:
        return derive_events(
            snap.values["messages"],
            reviews=snap.values.get("reviews") or {},
            pending=self._pending(snap),
            start=start,
            errors=self._errors.get(thread_id, [])[errors_from:],
        )

    def _turn(self, thread_id: str, before: int, errors_before: int) -> TurnResponse:
        snap = self._snapshot(thread_id)
        events = self._events(thread_id, snap, before, errors_before)
        pending = self._pending(snap)
        if pending and not any(
            isinstance(e, ApprovalRequestEvent) and e.interrupt_id == pending["interrupt_id"]
            for e in events
        ):
            # A second guarded call of the same message became pending after a decision.
            events.append(
                ApprovalRequestEvent(
                    type="approval_request",
                    tool_call_id=pending["tool_call_id"],
                    interrupt_id=pending["interrupt_id"],
                    action=PendingAction.model_validate(pending["action"]),
                )
            )
        return TurnResponse(events=events, state=self._state(snap))

    def _state(self, snap) -> ThreadState:
        v = snap.values
        version = self.billing.settings().version
        findings = [
            PlanFindings.model_validate({**f, "stale": f["settings_version"] != version})
            for f in (v.get("findings") or {}).values()
        ]
        pending = self._pending(snap)
        return ThreadState(
            active_plan_id=v.get("active_plan_id"),
            active_customer=v.get("active_customer"),
            findings=findings,
            proposals=[
                Proposal.model_validate(p)
                for p in sorted((v.get("proposals") or {}).values(), key=lambda p: p["proposal_id"])
            ],
            pending_approval=(
                PendingApproval(
                    interrupt_id=pending["interrupt_id"],
                    action=PendingAction.model_validate(pending["action"]),
                )
                if pending
                else None
            ),
            actions=[AuditAction.model_validate(a) for a in self.billing.list_actions()],
            settings_version=version,
        )


def _error_text(e: Exception) -> str:
    text = text_of(str(e)).strip() or type(e).__name__
    return text if len(text) < 500 else text[:500] + "..."


_shared: RealAgentService | None = None
_shared_lock = threading.Lock()


def create_service(
    llm: BaseChatModel | Callable[[], BaseChatModel] | None = None,
    *,
    data_dir: Path | None = None,
    state_dir: Path | None = None,
    clock: Callable[[], date] = date.today,
) -> RealAgentService:
    """The service the app runs: SqliteSaver under state_dir (D3, D4) and the configured LLM.

    Called with no arguments (what the app does) it is a process-wide singleton: several first
    requests arriving together must not open several connections to the same database and
    initialize it at once ("database is locked"). With arguments (tests) it builds a
    new one.
    """
    global _shared
    with _shared_lock:
        if llm is None and data_dir is None and state_dir is None and clock == date.today:
            if _shared is None:
                _shared = _build(None, None, None, clock)
            return _shared
        return _build(llm, data_dir, state_dir, clock)


def _build(llm, data_dir, state_dir, clock) -> RealAgentService:
    from app.config import settings
    from app.llm import get_llm

    state = Path(state_dir or settings.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    # A busy timeout and WAL make a second connection wait instead of failing; the tables are
    # created now, not inside the first request.
    conn = sqlite3.connect(state / "checkpoints.db", check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    saver = SqliteSaver(conn)
    saver.setup()
    billing = Billing(data_dir or settings.data_dir, state, clock)
    return RealAgentService(billing, llm or get_llm, saver)
