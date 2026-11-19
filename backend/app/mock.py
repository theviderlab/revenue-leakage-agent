"""Mock backend (D29): a keyword-scripted conversation, no LLM and no detector.

It walks the whole flow (investigate, propose, apply, approve / reject, rollback) with the Base
ground-truth numbers of plan PLN-1001, and returns the same models as the real backend will.
State lives in memory: the audit log and settings are global (D5, D11), the rest is per thread.
"""

import re
import threading
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from app.schemas import (
    ApprovalRequestEvent,
    AssistantMessageEvent,
    AuditAction,
    CreateThreadResponse,
    ErrorEvent,
    Event,
    FindingsEvent,
    FindingsPayload,
    PendingAction,
    PendingApproval,
    Period,
    Plan,
    PlanFindings,
    Proposal,
    ProposalEvent,
    SettingInfo,
    SettingsResponse,
    SettingsUpdate,
    ThreadState,
    ToolCallEvent,
    ToolResultEvent,
    Totals,
    TurnResponse,
    Unexplained,
    UserMessageEvent,
)
from app.service import Conflict, InvalidSettings, ThreadNotFound

PLAN_ID = "PLN-1001"
CUSTOMER = "Alder Creek Logistics"
MONTHLY = 7000.0
DATA_THROUGH = date(2026, 6, 30)
# Base ground truth: the invoice behind each billed month; 2026-02 is missing and 2026-05 is
# overbilled.
BASE_INVOICES = {
    12: "INV-1001",
    1: "INV-1002",
    3: "INV-1004",
    4: "INV-1005",
    5: "INV-1006",
    6: "INV-1007",
}
MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("january", "enero"),
            ("february", "febrero"),
            ("march", "marzo"),
            ("april", "abril"),
            ("may", "mayo"),
            ("june", "junio"),
            ("july", "julio"),
            ("august", "agosto"),
            ("september", "septiembre"),
            ("october", "octubre"),
            ("november", "noviembre"),
            ("december", "diciembre"),
        ],
        start=1,
    )
    for name in names
}

PLANS = [
    Plan(
        plan_id="PLN-1001",
        customer_name="Alder Creek Logistics",
        total_value=84000,
        currency="USD",
        cadence="Monthly",
        start_date=date(2025, 12, 1),
        amends=None,
        entitlements=["Analytics", "Support"],
        notes="12-month SaaS; 7,000 USD per month, billed in advance",
        source="data",
    ),
    Plan(
        plan_id="PLN-2001",
        customer_name="Vertex Dynamics",
        total_value=84000,
        currency="USD",
        cadence="Quarterly",
        start_date=date(2025, 5, 15),
        amends=None,
        entitlements=["Analytics", "Premium Support"],
        notes="Quarterly target 21,000 USD; superseded by PLN-2001-A1 (see amendment)",
        source="data",
    ),
    Plan(
        plan_id="PLN-2001-A1",
        customer_name="Vertex Dynamics",
        total_value=112000,
        currency="USD",
        cadence="Quarterly",
        start_date=date(2025, 7, 1),
        amends="PLN-2001",
        entitlements=["Analytics", "Premium Support"],
        notes="Amendment effective 2025-07-01. Quarterly target now 28,000 USD",
        source="data",
    ),
    Plan(
        plan_id="PLN-3001",
        customer_name="Lumen Health",
        total_value=120000,
        currency="USD",
        cadence="Annual",
        start_date=date(2025, 7, 15),
        amends=None,
        entitlements=["Analytics", "Consulting"],
        notes="Enterprise annual; 120,000 USD for 12 months from 2025-07-15",
        source="data",
    ),
]

SETTING_DEFS: list[tuple[str, str | bool | None, list[str | bool] | None]] = [
    ("as_of", None, None),
    ("data_up_to_date", None, [True, False]),
    ("period_calendar", "anchored", ["anchored", "calendar"]),
    ("amendment_proration", "prorate", ["prorate", "full_period"]),
    ("auto_renewal", False, [False, True]),
]

GRAPH = """graph TD
    start([start]) --> agent
    agent -->|tool calls| tools
    agent -->|apply / rollback| human_review
    tools --> agent
    human_review --> tools
    agent -->|no tool calls| finish([end])
"""

HELP = (
    "This is the scripted mock. Try: *check plan PLN-1001 for revenue leakage*, then *create a "
    "make-good invoice for February 2026*, *yes, apply it*, and *roll it back*."
)


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


@dataclass
class _Thread:
    thread_id: str
    events: list[Event] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    pending: PendingApproval | None = None
    pending_call_id: str = ""
    active_plan_id: str | None = None
    active_customer: str | None = None
    # Settings version the thread's findings were calculated with; None = no findings yet.
    findings_version: int | None = None
    calls: int = 0
    interrupts: int = 0


class MockService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._threads: dict[str, _Thread] = {}
        self._audit: list[AuditAction] = []
        self._mg_counter = 0
        self._version = 1
        self._values: dict[str, str | bool | date | None] = {k: None for k, _, _ in SETTING_DEFS}

    # --- plans and settings ---------------------------------------------------------------

    def list_plans(self) -> list[Plan]:
        return [p.model_copy(deep=True) for p in PLANS]

    def get_settings(self) -> SettingsResponse:
        with self._lock:
            return self._settings_response()

    def _settings_response(self) -> SettingsResponse:
        infos = []
        for key, default, valid in SETTING_DEFS:
            value = self._values[key]
            infos.append(
                SettingInfo(
                    key=key,  # type: ignore[arg-type]
                    value=value.isoformat() if isinstance(value, date) else value,
                    default=default,
                    valid_values=valid,
                )
            )
        return SettingsResponse(version=self._version, data_through=DATA_THROUGH, settings=infos)

    def update_settings(self, update: SettingsUpdate) -> SettingsResponse:
        with self._lock:
            if any(t.pending for t in self._threads.values()):
                raise Conflict("There is a pending action; decide it before changing settings")
            if update.as_of is not None and update.as_of > date.today():
                raise InvalidSettings("as_of cannot be later than today")
            changed = update.model_fields_set
            for key in changed:
                self._values[key] = getattr(update, key)
            if changed:
                self._version += 1
            return self._settings_response()

    # --- threads --------------------------------------------------------------------------

    def create_thread(self) -> CreateThreadResponse:
        with self._lock:
            thread = _Thread(thread_id=f"thr-{uuid4().hex[:8]}")
            self._threads[thread.thread_id] = thread
            return CreateThreadResponse(
                thread_id=thread.thread_id, events=[], state=self._state(thread)
            )

    def get_thread(self, thread_id: str) -> TurnResponse:
        with self._lock:
            thread = self._get(thread_id)
            return TurnResponse(
                events=[e.model_copy(deep=True) for e in thread.events], state=self._state(thread)
            )

    def graph(self) -> str:
        return GRAPH

    def send_message(self, thread_id: str, text: str) -> TurnResponse:
        with self._lock:
            thread = self._get(thread_id)
            if thread.pending:
                raise Conflict("There is a pending action; approve or reject it first")
            turn: list[Event] = [UserMessageEvent(type="user_message", text=text)]
            turn += self._script(thread, text)
            return self._finish(thread, turn)

    def decide(
        self, thread_id: str, interrupt_id: str, decision: Literal["approve", "reject"]
    ) -> TurnResponse:
        with self._lock:
            thread = self._get(thread_id)
            pending = thread.pending
            if pending is None or pending.interrupt_id != interrupt_id:
                raise Conflict("interrupt_id does not match the pending action")
            thread.pending = None
            action = pending.action
            call_id = thread.pending_call_id
            name = action.kind
            if decision == "reject":
                if action.proposal:
                    self._proposal(thread, action.proposal.proposal_id).status = "rejected"
                turn: list[Event] = [
                    ToolResultEvent(
                        type="tool_result",
                        tool_call_id=call_id,
                        name=name,
                        status="rejected_by_user",
                        result={"message": "Rejected by the user; nothing was written"},
                    ),
                    AssistantMessageEvent(
                        type="assistant_message",
                        text="Understood, nothing was written to the sandbox.",
                    ),
                ]
            elif action.kind == "apply":
                turn = self._do_apply(thread, call_id, action)
            else:
                turn = self._do_rollback(thread, call_id, action)
            return self._finish(thread, turn)

    # --- state ----------------------------------------------------------------------------

    def _get(self, thread_id: str) -> _Thread:
        thread = self._threads.get(thread_id)
        if thread is None:
            raise ThreadNotFound(thread_id)
        return thread

    def _finish(self, thread: _Thread, turn: list[Event]) -> TurnResponse:
        thread.events += [e.model_copy(deep=True) for e in turn]
        return TurnResponse(events=turn, state=self._state(thread))

    def _state(self, thread: _Thread) -> ThreadState:
        findings = []
        if thread.findings_version is not None:
            findings = [self._plan_findings(thread.findings_version)]
        return ThreadState(
            active_plan_id=thread.active_plan_id,
            active_customer=thread.active_customer,
            findings=findings,
            proposals=[p.model_copy(deep=True) for p in thread.proposals],
            pending_approval=thread.pending.model_copy(deep=True) if thread.pending else None,
            actions=[a.model_copy(deep=True) for a in self._audit],
            settings_version=self._version,
        )

    def _proposal(self, thread: _Thread, proposal_id: str) -> Proposal:
        return next(p for p in thread.proposals if p.proposal_id == proposal_id)

    def _active_actions(self) -> list[AuditAction]:
        return [a for a in self._audit if a.rolled_back_at is None]

    def _plan_findings(self, version: int) -> PlanFindings:
        """PLN-1001 Base ground truth, with the active make-goods of the sandbox as overlay (D6)."""
        fixed = {
            a.payload["period_start"]: a.action_id
            for a in self._active_actions()
            if a.type == "make_good_invoice" and a.plan_id == PLAN_ID
        }
        periods = []
        for i in range(12):
            year = 2025 + (11 + i) // 12
            month = (11 + i) % 12 + 1
            period_start = date(year, month, 1)
            period_end = _month_end(year, month)
            in_data = (year, month) <= (2026, 6)
            invoice_id = BASE_INVOICES.get(month) if in_data else None
            overbilled = invoice_id == "INV-1006"
            invoice_ids = [invoice_id] if invoice_id else []
            if period_start.isoformat() in fixed:
                invoice_ids.append(fixed[period_start.isoformat()])
            flags: list[str] = []
            if invoice_id == "INV-1005":
                flags.append("unpaid")
            invoiced = (8500.0 if overbilled else MONTHLY) if invoice_ids else 0.0
            net = invoiced
            if invoice_ids:
                status = "overbilled" if overbilled else "ok"
            elif period_start <= DATA_THROUGH:
                status = "missing"
            else:
                status = "no_data"
            periods.append(
                Period(
                    period_start=period_start,
                    period_end=period_end,
                    status=status,
                    expected=MONTHLY,
                    invoiced=invoiced,
                    credited=0.0,
                    net=net,
                    diff=net - MONTHLY,
                    invoice_ids=invoice_ids,
                    memo_ids=[],
                    flags=flags,
                )
            )
        counted = [p for p in periods if p.status != "no_data"]
        net = sum(p.net for p in counted)
        expected = sum(p.expected for p in counted)
        return PlanFindings(
            plan_id=PLAN_ID,
            confidence="high",
            confidence_reasons=[],
            periods=periods,
            totals=Totals(expected=expected, net=net, diff=net - expected),
            settings_version=version,
            stale=version != self._version,
        )

    def _findings_event(self, thread: _Thread, call_id: str) -> FindingsEvent:
        thread.findings_version = self._version
        plan = self._plan_findings(self._version)
        return FindingsEvent(
            type="findings",
            tool_call_id=call_id,
            findings=FindingsPayload(
                plans=[plan],
                totals=plan.totals.model_copy(),
                assumptions=[
                    "Billing is in advance: each invoice belongs to the period containing its "
                    "issue date",
                    f"Cutoff 2026-06-30 (inferred: last issue date in the data, {DATA_THROUGH})",
                    "period_calendar = anchored (default), amendment_proration = prorate "
                    "(default), auto_renewal = false (default)",
                ],
                unexplained=[
                    Unexplained(
                        record_id="INV-1003",
                        kind="invoice",
                        reason="no_fx_rate",
                        amount=400,
                        currency="EUR",
                        candidates=[],
                    ),
                    Unexplained(
                        record_id="INV-4001",
                        kind="invoice",
                        reason="no_plan",
                        amount=15000,
                        currency="USD",
                        candidates=["PLN-2001-A1"],
                    ),
                ],
            ),
        )

    # --- script ---------------------------------------------------------------------------

    def _call(self, thread: _Thread, name: str, args: dict[str, Any]) -> ToolCallEvent:
        thread.calls += 1
        return ToolCallEvent(
            type="tool_call", tool_call_id=f"call_{thread.calls}", name=name, args=args
        )

    def _say(self, text: str) -> AssistantMessageEvent:
        return AssistantMessageEvent(type="assistant_message", text=text)

    def _script(self, thread: _Thread, text: str) -> list[Event]:
        low = text.lower()
        if re.search(r"\b(error|crash|fail\w*)\b", low):
            return [ErrorEvent(type="error", message="Scripted failure: the LLM is unavailable")]
        if re.search(r"roll (it )?back|rollback|undo|revert|deshac\w*", low):
            return self._ask_rollback(thread, text)
        plan_ids = re.findall(r"pln-\d{4}(?:-a\d)?", low)
        other_plan = next((p.upper() for p in plan_ids if p != "pln-1001"), None)
        if re.search(r"make[- ]?good|propose|propon\w*|crea\w* .*(invoice|factura)", low):
            return self._propose(thread, low, other_plan)
        if re.search(r"\b(apply|yes|si|sí|aplic\w*|confirm\w*)\b", low):
            return self._ask_apply(thread)
        if re.search(r"check|investig\w*|leak\w*|anomal\w*|fuga|revis\w*|pln-1001", low):
            return self._investigate(thread, other_plan)
        if "currency" in low or "moneda" in low:
            if thread.active_plan_id:
                return [self._say(f"Plan {PLAN_ID} is in USD (about {_money(MONTHLY)} USD/month).")]
            return [self._say("Which plan do you mean? Ask me to check one first.")]
        return [self._say(HELP)]

    def _investigate(self, thread: _Thread, other_plan: str | None) -> list[Event]:
        plan = other_plan or PLAN_ID
        call = self._call(thread, "detect_anomalies", {"plan_id": plan})
        if other_plan:
            return [
                call,
                ToolResultEvent(
                    type="tool_result",
                    tool_call_id=call.tool_call_id,
                    name=call.name,
                    status="error",
                    result={"status": "error", "error": "The mock script only covers PLN-1001"},
                ),
                self._say(f"The mock script only covers plan {PLAN_ID}, not {plan}."),
            ]
        thread.active_plan_id, thread.active_customer = PLAN_ID, CUSTOMER
        findings = self._findings_event(thread, call.tool_call_id)
        plan_findings = findings.findings.plans[0]
        missing = [p for p in plan_findings.periods if p.status == "missing"]
        return [
            call,
            ToolResultEvent(
                type="tool_result",
                tool_call_id=call.tool_call_id,
                name=call.name,
                status="ok",
                result={
                    "plan_id": PLAN_ID,
                    "missing": [p.period_start.isoformat() for p in missing],
                },
            ),
            findings,
            self._say(
                self._summary(plan_findings)
                + " I can propose a make-good invoice for the missing month if you ask."
            ),
        ]

    def _summary(self, f: PlanFindings) -> str:
        missing = [p.period_start.strftime("%B %Y") for p in f.periods if p.status == "missing"]
        overbilled = [
            f"{p.period_start:%B %Y} by {_money(p.diff)}"
            for p in f.periods
            if p.status == "overbilled"
        ]
        if not missing:
            return f"Plan **{f.plan_id}** has no missing periods: nothing is leaking."
        return (
            f"Plan **{f.plan_id}** ({CUSTOMER}) expects {_money(f.totals.expected)} USD and was "
            f"billed {_money(f.totals.net)} USD, so **{_money(-f.totals.diff)} USD** is missing "
            f"({', '.join(missing)}; {_money(MONTHLY)} each); {', '.join(overbilled)} is "
            f"overbilled."
        )

    def _propose(self, thread: _Thread, low: str, other_plan: str | None) -> list[Event]:
        month = next((m for name, m in MONTHS.items() if name in low), None)
        year = 2026
        year_explicit = False
        if m := re.search(r"(20(?:25|26))-(\d\d)", low):
            year, month = int(m.group(1)), int(m.group(2))
            year_explicit = True
        month = month or 2
        if month == 12 and not year_explicit:
            year = 2025
        period_start = date(year, month, 1)
        call = self._call(
            thread,
            "propose_make_good_invoice",
            {
                "plan_id": other_plan or PLAN_ID,
                "period_start": period_start.isoformat(),
                "amount": MONTHLY,
            },
        )
        error: str | None = None
        if other_plan:
            error = f"Plan {other_plan} has low confidence or a pending decision: no proposals"
        elif (
            not 1 <= month <= 12
            or not date(2025, 12, 1) <= period_start <= date(2026, 11, 1)
            or month in BASE_INVOICES
            or period_start > DATA_THROUGH
            or any(
                a.type == "make_good_invoice"
                and a.payload["period_start"] == period_start.isoformat()
                for a in self._active_actions()
            )
        ):
            error = f"{period_start:%B %Y} is not a missing period of {PLAN_ID}"
        elif any(
            p.status == "pending" and p.args["period_start"] == period_start.isoformat()
            for p in thread.proposals
        ):
            error = f"There is already a pending proposal for {period_start:%B %Y}"
        if error:
            return [
                call,
                ToolResultEvent(
                    type="tool_result",
                    tool_call_id=call.tool_call_id,
                    name=call.name,
                    status="error",
                    result={"status": "error", "error": error},
                ),
                self._say(f"I can't propose that: {error}."),
            ]
        proposal = Proposal(
            proposal_id=f"PRP-{len(thread.proposals) + 1:03d}",
            type="make_good_invoice",
            plan_id=PLAN_ID,
            args=call.args,
            evidence=[
                f"Plan {PLAN_ID} expects {_money(MONTHLY)} USD per month (84,000.00 / 12)",
                f"No invoice with issue_date in {period_start:%Y-%m}",
                f"Finding {PLAN_ID}:missing:{period_start.isoformat()}: diff "
                f"-{_money(MONTHLY)} USD",
            ],
            reason=f"Recover the missing {period_start:%B %Y} billing of plan {PLAN_ID}",
            settings_version=self._version,
            status="pending",
        )
        thread.proposals.append(proposal)
        thread.active_plan_id, thread.active_customer = PLAN_ID, CUSTOMER
        return [
            call,
            ToolResultEvent(
                type="tool_result",
                tool_call_id=call.tool_call_id,
                name=call.name,
                status="ok",
                result=proposal.model_dump(mode="json"),
            ),
            ProposalEvent(
                type="proposal", tool_call_id=call.tool_call_id, proposal=proposal.model_copy()
            ),
            self._say(
                f"Proposal **{proposal.proposal_id}**: make-good invoice of {_money(MONTHLY)} USD "
                f"for {period_start:%B %Y}. Say *apply* when you want me to ask for approval."
            ),
        ]

    def _ask_apply(self, thread: _Thread) -> list[Event]:
        proposal = next((p for p in reversed(thread.proposals) if p.status == "pending"), None)
        if proposal is None:
            return [self._say("There is no pending proposal to apply. Ask me for one first.")]
        call = self._call(thread, "apply", {"proposal_id": proposal.proposal_id})
        action = PendingAction(
            kind="apply",
            proposal=proposal.model_copy(deep=True),
            action_id=None,
            summary=f"Apply {proposal.proposal_id}: {proposal.reason}",
            evidence=proposal.evidence,
        )
        return self._request_approval(thread, call, action)

    def _ask_rollback(self, thread: _Thread, text: str) -> list[Event]:
        active = self._active_actions()
        if not active:
            return [self._say("There are no applied actions to roll back.")]
        last = active[-1]
        call_args = {"action_id": last.action_id}
        asked = re.search(r"INV-MG-\d+", text.upper())
        if asked and asked.group() != last.action_id:
            call_args = {"action_id": asked.group()}
            call = self._call(thread, "rollback", call_args)
            error = f"Only the last active action can be rolled back: {last.action_id}"
            return [
                call,
                ToolResultEvent(
                    type="tool_result",
                    tool_call_id=call.tool_call_id,
                    name=call.name,
                    status="error",
                    result={"status": "error", "error": error},
                ),
                self._say(f"{error}."),
            ]
        call = self._call(thread, "rollback", call_args)
        action = PendingAction(
            kind="rollback",
            proposal=None,
            action_id=last.action_id,
            summary=f"Roll back {last.action_id}: {last.reason}",
            evidence=last.evidence,
        )
        return self._request_approval(thread, call, action)

    def _request_approval(
        self, thread: _Thread, call: ToolCallEvent, action: PendingAction
    ) -> list[Event]:
        thread.interrupts += 1
        interrupt_id = f"int-{thread.thread_id}-{thread.interrupts}"
        thread.pending = PendingApproval(interrupt_id=interrupt_id, action=action)
        thread.pending_call_id = call.tool_call_id
        return [
            call,
            ApprovalRequestEvent(
                type="approval_request",
                tool_call_id=call.tool_call_id,
                interrupt_id=interrupt_id,
                action=action.model_copy(deep=True),
            ),
        ]

    def _do_apply(self, thread: _Thread, call_id: str, action: PendingAction) -> list[Event]:
        assert action.proposal is not None
        proposal = self._proposal(thread, action.proposal.proposal_id)
        self._mg_counter += 1
        action_id = f"INV-MG-{self._mg_counter:03d}"
        issue = date.today()
        record = {
            "invoice_id": action_id,
            "plan_id": PLAN_ID,
            "customer_name": CUSTOMER,
            "description": f"Make-good invoice for {proposal.args['period_start'][:7]}",
            "period_start": proposal.args["period_start"],
            "issue_date": issue.isoformat(),
            "due_date": (issue + timedelta(days=15)).isoformat(),
            "amount_invoiced": proposal.args["amount"],
            "currency": "USD",
            "status": "unpaid",
            "action_id": action_id,
            "ledger_state": "active",
            "source": "sandbox",
        }
        self._audit.append(
            AuditAction(
                action_id=action_id,
                type="make_good_invoice",
                proposal_id=proposal.proposal_id,
                thread_id=thread.thread_id,
                plan_id=PLAN_ID,
                payload=record,
                evidence=proposal.evidence,
                reason=proposal.reason,
                applied_at=datetime.now(),
                rolled_back_at=None,
            )
        )
        proposal.status = "applied"
        turn: list[Event] = [
            ToolResultEvent(
                type="tool_result",
                tool_call_id=call_id,
                name="apply",
                status="ok",
                result={"status": "applied", "action_id": action_id},
            )
        ]
        return turn + self._recheck(thread, f"Applied: make-good invoice **{action_id}** written.")

    def _do_rollback(self, thread: _Thread, call_id: str, action: PendingAction) -> list[Event]:
        target = next(a for a in self._audit if a.action_id == action.action_id)
        target.rolled_back_at = datetime.now()
        turn: list[Event] = [
            ToolResultEvent(
                type="tool_result",
                tool_call_id=call_id,
                name="rollback",
                status="ok",
                result={"status": "rolled_back", "action_id": target.action_id},
            )
        ]
        return turn + self._recheck(thread, f"Rolled back **{target.action_id}**.")

    def _recheck(self, thread: _Thread, confirmation: str) -> list[Event]:
        """D23: apply / rollback recalculate the affected plan, unless findings are stale."""
        if thread.findings_version is not None and thread.findings_version != self._version:
            return [self._say(confirmation + " Your findings are stale: ask me to re-check.")]
        call = self._call(thread, "detect_anomalies", {"plan_id": PLAN_ID})
        findings = self._findings_event(thread, call.tool_call_id)
        return [
            call,
            ToolResultEvent(
                type="tool_result",
                tool_call_id=call.tool_call_id,
                name=call.name,
                status="ok",
                result={"plan_id": PLAN_ID, "diff": findings.findings.totals.diff},
            ),
            findings,
            self._say(f"{confirmation} {self._summary(findings.findings.plans[0])}"),
        ]
