"""Tool wrappers over the domain (D15, D27).

Each tool is a plain function `(ctx, args) -> Outcome`; the `tools` node runs them one at a
time, in order, so proposal numbering is deterministic (a ToolNode would run them in parallel).
Domain errors come back to the LLM as `{"status": "error", "error": ...}`, never as exceptions.
`apply` and `rollback` are declared here for the LLM but run only in `human_review`.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from app.agent.mapping import findings_payload, proposal_dict
from app.domain.billing import Billing
from app.domain.errors import DomainError, NotFoundError

GUARDED = {"apply", "rollback"}


@dataclass
class Ctx:
    """What a tool reads (the state at the start of the step) and the updates it produces."""

    billing: Billing
    thread_id: str
    proposals: dict[str, dict]
    freshness_asked: bool
    update: dict[str, Any] = field(default_factory=dict)

    def put(self, key: str, value: Any) -> None:
        self.update[key] = value

    def merge(self, key: str, value: dict) -> None:
        self.update[key] = {**self.update.get(key, {}), **value}


@dataclass
class Outcome:
    content: dict[str, Any]  # what the LLM sees
    status: str = "ok"  # ok | error
    # Structured data for the API events: result, findings, proposal.
    artifact: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        return json.dumps(self.content, ensure_ascii=False, separators=(",", ":"), default=str)


def error(message: str) -> Outcome:
    return Outcome({"status": "error", "error": message}, "error")


# --- argument models (the schemas the LLM sees) --------------------------------------------


class NoArgs(BaseModel):
    pass


class PlanArgs(BaseModel):
    plan_id: str = Field(description="Billing plan ID, for example PLN-1001")


class QueryInvoicesArgs(BaseModel):
    plan_id: str | None = Field(None, description="Only invoices of this plan")
    customer_name: str | None = Field(None, description="Exact customer name, case-insensitive")
    date_from: str | None = Field(None, description="Issue date from, YYYY-MM-DD, inclusive")
    date_to: str | None = Field(None, description="Issue date to, YYYY-MM-DD, inclusive")


class FxArgs(BaseModel):
    amount: float
    from_ccy: str = Field(description="ISO currency code, for example EUR")
    to_ccy: str = Field(description="ISO currency code, for example USD")
    on_date: str = Field(description="Rate date, YYYY-MM-DD (exact date only)")


class DetectArgs(BaseModel):
    plan_id: str | None = Field(None, description="One plan; omit to analyze all plans")


class ConceptArgs(BaseModel):
    key: str = Field(description="Concept key, for example credit_memo or period_calendar")


class MakeGoodArgs(BaseModel):
    plan_id: str
    period_start: str = Field(description="Start of the period it covers, YYYY-MM-DD")
    amount: float = Field(description="In the plan's currency; at most the period's shortfall")


class CreditMemoArgs(BaseModel):
    invoice_id: str
    amount: float = Field(description="In the plan's currency; at most the period's excess")


class AmendmentArgs(BaseModel):
    plan_id: str
    effective_from: str = Field(description="Start of a period of the plan, YYYY-MM-DD")
    change_set: dict[str, Any] = Field(
        description="Fields to change: total_value, cadence, entitlements, notes"
    )


class LinkArgs(BaseModel):
    invoice_id: str
    plan_id: str = Field(description="One of the candidate plans of the unexplained invoice")


class InvoiceArgs(BaseModel):
    invoice_id: str


class ApplyArgs(BaseModel):
    proposal_id: str = Field(description="For example PRP-001")


class RollbackArgs(BaseModel):
    action_id: str = Field(description="For example INV-MG-001; must be the last active action")


# --- tools ---------------------------------------------------------------------------------


def list_plans(ctx: Ctx, a: NoArgs) -> Outcome:
    return Outcome({"plans": ctx.billing.list_plans()})


def load_plan(ctx: Ctx, a: PlanArgs) -> Outcome:
    plan = ctx.billing.load_plan(a.plan_id)
    ctx.put("active_plan_id", plan["plan_id"])
    ctx.put("active_customer", plan["customer_name"])
    return Outcome(plan)


def query_invoices(ctx: Ctx, a: QueryInvoicesArgs) -> Outcome:
    rows = ctx.billing.query_invoices(a.plan_id, a.customer_name, a.date_from, a.date_to)
    return Outcome({"count": len(rows), "invoices": rows})


def fx_convert(ctx: Ctx, a: FxArgs) -> Outcome:
    return Outcome(ctx.billing.fx_convert(a.amount, a.from_ccy, a.to_ccy, a.on_date))


def get_concept(ctx: Ctx, a: ConceptArgs) -> Outcome:
    return Outcome(ctx.billing.get_concept(a.key))


def detect_anomalies(ctx: Ctx, a: DetectArgs) -> Outcome:
    det = ctx.billing.detect_anomalies(a.plan_id)
    payload = findings_payload(det)
    ctx.merge("findings", {p["plan_id"]: p for p in payload["plans"]})
    if a.plan_id:
        plan = ctx.billing.snapshot().plan(a.plan_id)
        ctx.put("active_plan_id", plan.plan_id)
        ctx.put("active_customer", plan.customer_name)
    fresh = det.freshness
    ask = fresh["needs_question"] and not ctx.freshness_asked
    if ask:
        ctx.put("freshness_asked", True)
    body = det.to_dict()
    body.pop("findings")
    body["freshness"] = {**fresh, "ask_user_about_freshness": ask}
    result = {
        "scope": det.scope,
        "totals": payload["totals"],
        "plans": [
            {"plan_id": p["plan_id"], "confidence": p["confidence"], "totals": p["totals"]}
            for p in payload["plans"]
        ],
        "unexplained": [x["record_id"] for x in payload["unexplained"]],
    }
    return Outcome(body, artifact={"findings": payload, "result": result})


def _proposal_outcome(ctx: Ctx, make: Callable[[], Any]) -> Outcome:
    p = make()
    taken = len(ctx.proposals) + len(ctx.update.get("proposals", {}))
    proposal_id = f"PRP-{taken + 1:03d}"
    d = proposal_dict(proposal_id, p)
    ctx.merge("proposals", {proposal_id: d})
    if p.plan_id:
        plan = ctx.billing.snapshot().plan(p.plan_id)
        ctx.put("active_plan_id", plan.plan_id)
        ctx.put("active_customer", plan.customer_name)
    return Outcome({"status": "proposed", **d, "payload": p.payload}, artifact={"proposal": d})


def propose_make_good_invoice(ctx: Ctx, a: MakeGoodArgs) -> Outcome:
    return _proposal_outcome(
        ctx, lambda: ctx.billing.propose_make_good_invoice(a.plan_id, a.period_start, a.amount)
    )


def propose_credit_memo(ctx: Ctx, a: CreditMemoArgs) -> Outcome:
    return _proposal_outcome(ctx, lambda: ctx.billing.propose_credit_memo(a.invoice_id, a.amount))


def propose_plan_amendment(ctx: Ctx, a: AmendmentArgs) -> Outcome:
    return _proposal_outcome(
        ctx,
        lambda: ctx.billing.propose_plan_amendment(a.plan_id, a.effective_from, a.change_set),
    )


def propose_link_invoice(ctx: Ctx, a: LinkArgs) -> Outcome:
    return _proposal_outcome(ctx, lambda: ctx.billing.propose_link_invoice(a.invoice_id, a.plan_id))


def propose_dismiss_invoice(ctx: Ctx, a: InvoiceArgs) -> Outcome:
    return _proposal_outcome(ctx, lambda: ctx.billing.propose_dismiss_invoice(a.invoice_id))


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[Ctx, Any], Outcome] | None  # None = guarded, runs in human_review


SPECS: list[ToolSpec] = [
    ToolSpec("list_plans", "List all billing plans with customer, currency, cadence and total.", NoArgs, list_plans),
    ToolSpec("load_plan", "Read one plan: terms, versions (amendments) and whether another plan replaces it.", PlanArgs, load_plan),
    ToolSpec("query_invoices", "Filter invoices by plan, customer and issue date; each comes with its credit memos.", QueryInvoicesArgs, query_invoices),
    ToolSpec("fx_convert", "Convert an amount on an exact date; only published rates and direction exist.", FxArgs, fx_convert),
    ToolSpec("detect_anomalies", "Investigate revenue leakage: expected vs invoiced per period, net of credit memos, with statuses, totals, confidence, assumptions and unexplained records. Deterministic. One plan or all.", DetectArgs, detect_anomalies),
    ToolSpec("get_concept", "Definition of a billing concept, rule, setting, status or reason, by key.", ConceptArgs, get_concept),
    ToolSpec("propose_make_good_invoice", "Draft a make-good invoice for ONE missing or underbilled period. Only when the user asks.", MakeGoodArgs, propose_make_good_invoice),
    ToolSpec("propose_credit_memo", "Draft a credit memo on an invoice of an overbilled period. Only when the user asks.", CreditMemoArgs, propose_credit_memo),
    ToolSpec("propose_plan_amendment", "Draft a plan amendment (new version of the same plan) effective from the start of a period. Only when the user asks.", AmendmentArgs, propose_plan_amendment),
    ToolSpec("propose_link_invoice", "Draft linking an invoice that has no plan to one of its candidate plans. Only when the user asks.", LinkArgs, propose_link_invoice),
    ToolSpec("propose_dismiss_invoice", "Draft dismissing an invoice that has no plan. Only when the user asks.", InvoiceArgs, propose_dismiss_invoice),
    ToolSpec("apply", "Apply a pending proposal to the sandbox. The user is asked to approve in the interface; nothing is written before.", ApplyArgs, None),
    ToolSpec("rollback", "Undo an applied action (only the last active one). The user is asked to approve.", RollbackArgs, None),
]  # fmt: skip
BY_NAME = {s.name: s for s in SPECS}


def llm_tools() -> list[StructuredTool]:
    """Schemas for `bind_tools`. They are never invoked by LangChain: the graph runs them."""

    def _never(**kwargs: Any) -> str:
        raise RuntimeError("tools are executed by the graph")

    return [
        StructuredTool.from_function(
            func=_never, name=s.name, description=s.description, args_schema=s.args
        )
        for s in SPECS
    ]


def run_tool(ctx: Ctx, name: str, raw_args: dict[str, Any]) -> Outcome:
    """Run one non-guarded tool; every failure becomes an error result for the LLM."""
    spec = BY_NAME.get(name)
    if spec is None or spec.run is None:
        return error(f"unknown tool '{name}'; tools: {', '.join(BY_NAME)}")
    try:
        args = spec.args.model_validate(raw_args or {})
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        return error(f"invalid arguments for {name}: {problems}")
    try:
        return spec.run(ctx, args)
    except (DomainError, NotFoundError) as e:
        return error(str(e))
