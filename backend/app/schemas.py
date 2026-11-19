"""API contract (D9, D14, D24, D25, D28, D34).

Response models have no field defaults on purpose: with `separate_input_output_schemas=False` a
default makes the field optional in the generated TypeScript, which breaks narrowing on `type`.
Amounts are floats rounded to cents by the producer; the client only formats them (D31).
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

ProposalType = Literal[
    "make_good_invoice", "credit_memo", "plan_amendment", "link_invoice", "dismiss_invoice"
]
ProposalStatus = Literal["pending", "applied", "rejected", "invalid"]
PeriodStatus = Literal["ok", "missing", "underbilled", "overbilled", "pending_decision", "no_data"]
Confidence = Literal["high", "low"]
Source = Literal["data", "sandbox"]


class ErrorResponse(BaseModel):
    detail: str


# --- Findings (D19) ---------------------------------------------------------------------------


class Period(BaseModel):
    period_start: date
    period_end: date
    status: PeriodStatus
    expected: float
    invoiced: float
    credited: float
    net: float
    diff: float
    invoice_ids: list[str]
    memo_ids: list[str]
    flags: list[str]


class Totals(BaseModel):
    expected: float
    net: float
    diff: float


class Unexplained(BaseModel):
    record_id: str
    kind: Literal["invoice", "memo"]
    reason: Literal[
        "no_plan", "no_fx_rate", "outside_term", "after_as_of", "orphan_memo", "dismissed"
    ]
    amount: float
    currency: str
    candidates: list[str]


class PlanFindings(BaseModel):
    plan_id: str
    confidence: Confidence
    # Settings that lower the confidence (D20); empty when high.
    confidence_reasons: list[str]
    periods: list[Period]
    totals: Totals
    settings_version: int
    stale: bool


class FindingsPayload(BaseModel):
    plans: list[PlanFindings]
    totals: Totals
    assumptions: list[str]
    unexplained: list[Unexplained]


# --- Proposals, pending action and audit log (D25, D34) ----------------------------------------


class Proposal(BaseModel):
    proposal_id: str
    type: ProposalType
    plan_id: str | None
    args: dict[str, Any]
    evidence: list[str]
    reason: str
    settings_version: int
    status: ProposalStatus


class PendingAction(BaseModel):
    """What the approval card asks about: one shape for `apply` and `rollback`."""

    kind: Literal["apply", "rollback"]
    # apply: the proposal being applied. rollback: null.
    proposal: Proposal | None
    # rollback: the action being reverted. apply: null.
    action_id: str | None
    summary: str
    evidence: list[str]


class PendingApproval(BaseModel):
    interrupt_id: str
    action: PendingAction


class AuditAction(BaseModel):
    action_id: str
    type: ProposalType
    proposal_id: str
    thread_id: str
    plan_id: str | None
    payload: dict[str, Any]
    evidence: list[str]
    reason: str
    applied_at: datetime
    rolled_back_at: datetime | None


# --- Events (D28) ------------------------------------------------------------------------------


class UserMessageEvent(BaseModel):
    type: Literal["user_message"]
    text: str


class AssistantMessageEvent(BaseModel):
    type: Literal["assistant_message"]
    text: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"]
    tool_call_id: str
    name: str
    args: dict[str, Any]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"]
    tool_call_id: str
    name: str
    status: Literal["ok", "error", "rejected_by_user"]
    result: Any


class FindingsEvent(BaseModel):
    type: Literal["findings"]
    tool_call_id: str
    findings: FindingsPayload


class ProposalEvent(BaseModel):
    type: Literal["proposal"]
    tool_call_id: str
    proposal: Proposal


class ApprovalRequestEvent(BaseModel):
    type: Literal["approval_request"]
    tool_call_id: str
    interrupt_id: str
    action: PendingAction


class ErrorEvent(BaseModel):
    type: Literal["error"]
    message: str


Event = Annotated[
    UserMessageEvent
    | AssistantMessageEvent
    | ToolCallEvent
    | ToolResultEvent
    | FindingsEvent
    | ProposalEvent
    | ApprovalRequestEvent
    | ErrorEvent,
    Field(discriminator="type"),
]


# --- Thread state and responses (D9, D28) ------------------------------------------------------


class ThreadState(BaseModel):
    active_plan_id: str | None
    active_customer: str | None
    findings: list[PlanFindings]
    proposals: list[Proposal]
    pending_approval: PendingApproval | None
    # Global audit log: every thread sees the same actions (D5, D25).
    actions: list[AuditAction]
    settings_version: int


class TurnResponse(BaseModel):
    events: list[Event]
    state: ThreadState


class CreateThreadResponse(BaseModel):
    thread_id: str
    events: list[Event]
    state: ThreadState


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)


class DecisionRequest(BaseModel):
    interrupt_id: str
    decision: Literal["approve", "reject"]


# --- Plans and settings (D6, D12, D24) ---------------------------------------------------------


class Plan(BaseModel):
    plan_id: str
    customer_name: str
    total_value: float
    currency: str
    cadence: str
    start_date: date
    amends: str | None
    entitlements: list[str]
    notes: str
    source: Source


class SettingInfo(BaseModel):
    key: Literal[
        "as_of", "data_up_to_date", "period_calendar", "amendment_proration", "auto_renewal"
    ]
    # null = not set: the detector uses the default.
    value: str | bool | None
    default: str | bool | None
    # null = free value (as_of is an ISO date).
    valid_values: list[str | bool] | None


class SettingsResponse(BaseModel):
    version: int
    data_through: date
    settings: list[SettingInfo]


class SettingsUpdate(BaseModel):
    """Only the fields present in the body change; an explicit null restores the default."""

    as_of: date | None = None
    data_up_to_date: bool | None = None
    period_calendar: Literal["anchored", "calendar"] | None = None
    amendment_proration: Literal["prorate", "full_period"] | None = None
    auto_renewal: bool | None = None
