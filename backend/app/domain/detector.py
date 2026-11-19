"""Deterministic anomaly detector (D19): expected vs invoiced per period, net of credit memos.

No LLM. Works in the plan's currency. Every invoice and memo ends in a period or in the
`unexplained` residue: nothing disappears silently. "Today" is a parameter, never read here.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.domain.analysis_settings import ASSUMPTION_KEYS, SPECS, AnalysisSettings
from app.domain.errors import DomainError
from app.domain.fx import Conversion, convert
from app.domain.models import Invoice, Memo, Plan, Snapshot
from app.domain.periods import Period, generate_periods
from app.domain.util import num

FINDING_STATUSES = ("missing", "underbilled", "overbilled")
# Fixed rules the result always depends on (D20); keys are knowledge.md sections (D32).
FIXED_RULES = (
    "billing_in_advance",
    "term_12_months",
    "amount_per_period",
    "amendment_replaces_plan",
    "proration",
    "currency_conversion",
    "credit_memo_netting",
    "unpaid_invoices",
    "late_issuance",
    "entitlements",
)


@dataclass
class InvoiceLine:
    invoice_id: str
    amount: Decimal
    currency: str
    amount_in_plan_currency: Decimal
    issue_date: date
    status: str
    source: str
    days_from_period_start: int
    flags: list[str]
    conversion: Conversion | None = None

    def to_dict(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "amount": num(self.amount),
            "currency": self.currency,
            "amount_in_plan_currency": num(self.amount_in_plan_currency),
            "issue_date": self.issue_date.isoformat(),
            "status": self.status,
            "source": self.source,
            "days_from_period_start": self.days_from_period_start,
            "flags": self.flags,
            "conversion": self.conversion.to_dict() if self.conversion else None,
        }


@dataclass
class MemoLine:
    memo_id: str
    invoice_id: str
    amount: Decimal
    currency: str
    amount_in_plan_currency: Decimal
    source: str
    conversion: Conversion | None = None

    def to_dict(self) -> dict:
        return {
            "memo_id": self.memo_id,
            "invoice_id": self.invoice_id,
            "amount": num(self.amount),
            "currency": self.currency,
            "amount_in_plan_currency": num(self.amount_in_plan_currency),
            "source": self.source,
            "conversion": self.conversion.to_dict() if self.conversion else None,
        }


@dataclass
class PeriodResult:
    plan_id: str
    period: Period
    status: str
    invoiced: Decimal
    credited: Decimal
    invoices: list[InvoiceLine] = field(default_factory=list)
    memos: list[MemoLine] = field(default_factory=list)
    pending_invoice_ids: list[str] = field(default_factory=list)
    underlying_status: str | None = None  # the status before `pending_decision`

    @property
    def expected(self) -> Decimal:
        return self.period.expected

    @property
    def net(self) -> Decimal:
        return self.invoiced - self.credited

    @property
    def difference(self) -> Decimal:
        return self.net - self.expected

    @property
    def shortfall(self) -> Decimal:
        return max(self.expected - self.net, Decimal(0))

    @property
    def excess(self) -> Decimal:
        return max(self.net - self.expected, Decimal(0))

    @property
    def finding_id(self) -> str:
        return f"{self.plan_id}:{self.underlying_status or self.status}:{self.period.start}"

    def to_dict(self) -> dict:
        p = self.period
        return {
            "plan_id": self.plan_id,
            "period_start": p.start.isoformat(),
            "period_end": p.end.isoformat(),
            "status": self.status,
            "expected": num(self.expected),
            "invoiced": num(self.invoiced),
            "credited": num(self.credited),
            "net": num(self.net),
            "difference": num(self.difference),
            "proration": {
                "ratio": float(p.ratio),
                "prorated": p.ratio != 1,
                "full_amount": num(p.full_amount),
                "truncated_by": p.truncated_by,
            },
            "invoices": [i.to_dict() for i in self.invoices],
            "memos": [m.to_dict() for m in self.memos],
            "pending_invoice_ids": self.pending_invoice_ids,
            "underlying_status": self.underlying_status,
            "finding_id": self.finding_id,
        }


@dataclass
class ResidueItem:
    kind: str  # "invoice" or "memo"
    id: str
    reason: str
    plan_id: str
    customer_name: str | None
    amount: Decimal
    currency: str
    issue_date: date
    candidates: list[str] = field(default_factory=list)
    invoice_id: str | None = None  # for memos
    memo_ids: list[str] = field(default_factory=list)  # memos that follow an invoice
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "reason": self.reason,
            "plan_id": self.plan_id or None,
            "customer_name": self.customer_name,
            "amount": num(self.amount),
            "currency": self.currency,
            "issue_date": self.issue_date.isoformat(),
            "candidates": self.candidates,
            "invoice_id": self.invoice_id,
            "memo_ids": self.memo_ids,
            "detail": self.detail,
        }


def _zero_totals() -> dict[str, Decimal]:
    return {k: Decimal(0) for k in ("expected", "invoiced", "credited", "net", "difference")}


def _totals_dict(t: dict[str, Decimal]) -> dict:
    return {k: num(v) for k, v in t.items()}


@dataclass
class PlanResult:
    plan: Plan
    periods: list[PeriodResult]
    residue: list[ResidueItem]
    confidence: str = "high"
    low_because: list[str] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, Decimal]:
        t = _zero_totals()
        for r in self.periods:
            if r.status == "no_data":
                continue
            t["expected"] += r.expected
            t["invoiced"] += r.invoiced
            t["credited"] += r.credited
            t["net"] += r.net
            t["difference"] += r.difference
        return t

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan.plan_id,
            "customer_name": self.plan.customer_name,
            "currency": self.plan.currency,
            "confidence": self.confidence,
            "low_because": self.low_because,
            "totals": _totals_dict(self.totals),
            "periods": [r.to_dict() for r in self.periods],
        }


@dataclass
class Detection:
    scope: str  # "all" or a plan_id
    settings: AnalysisSettings
    as_of: date
    as_of_inferred: bool
    data_through: date
    data_up_to_date: bool
    plans: list[PlanResult]
    residue: list[ResidueItem]

    def plan(self, plan_id: str) -> PlanResult | None:
        return next((p for p in self.plans if p.plan.plan_id == plan_id), None)

    def period(self, plan_id: str, start: date) -> PeriodResult | None:
        p = self.plan(plan_id)
        return next((r for r in p.periods if r.period.start == start), None) if p else None

    def period_of_invoice(self, invoice_id: str) -> PeriodResult | None:
        for p in self.plans:
            for r in p.periods:
                if any(line.invoice_id == invoice_id for line in r.invoices):
                    return r
        return None

    def residue_of(self, invoice_id: str) -> ResidueItem | None:
        return next((x for x in self.residue if x.kind == "invoice" and x.id == invoice_id), None)

    @property
    def totals(self) -> dict[str, Any]:
        t = _zero_totals()
        for p in self.plans:
            for k, v in p.totals.items():
                t[k] += v
        currencies = {p.plan.currency for p in self.plans}
        out = _totals_dict(t)
        out["currency"] = currencies.pop() if len(currencies) == 1 else "mixed"
        return out

    @property
    def freshness(self) -> dict:
        gap = [r for p in self.plans for r in p.periods if r.status == "no_data"]
        return {
            "gap": bool(gap),
            "no_data_periods": len(gap),
            "amount_at_stake": num(sum((r.expected for r in gap), Decimal(0))),
            "data_up_to_date_fixed": self.settings.is_fixed("data_up_to_date"),
            "needs_question": bool(gap) and not self.settings.is_fixed("data_up_to_date"),
        }

    @property
    def assumptions(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "as_of_inferred": self.as_of_inferred,
            "data_through": self.data_through.isoformat(),
            "billing": "in_advance",
            "rules": list(FIXED_RULES),
            "settings": [
                {
                    "key": k,
                    "value": self.settings.effective(k) if k != "as_of" else self.as_of.isoformat(),
                    "fixed": self.settings.is_fixed(k),
                }
                for k in SPECS
            ],
        }

    def findings(self) -> list[dict]:
        out = []
        for p in self.plans:
            for r in p.periods:
                if r.status in (*FINDING_STATUSES, "pending_decision"):
                    out.append(
                        {
                            "id": r.finding_id,
                            "plan_id": p.plan.plan_id,
                            "type": r.underlying_status or r.status,
                            "status": r.status,
                            "period_start": r.period.start.isoformat(),
                            "period_end": r.period.end.isoformat(),
                            "expected": num(r.expected),
                            "net": num(r.net),
                            "difference": num(r.difference),
                            "currency": p.plan.currency,
                            "confidence": p.confidence,
                            "settings_version": self.settings.version,
                        }
                    )
        return out

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "settings_version": self.settings.version,
            "plans": [p.to_dict() for p in self.plans],
            "totals": self.totals,
            "unexplained": [x.to_dict() for x in self.residue],
            "assumptions": self.assumptions,
            "freshness": self.freshness,
            "findings": self.findings(),
        }


def _find_period(periods: list[Period], inv: Invoice) -> Period | None:
    if inv.period_start:
        return next((p for p in periods if p.start == inv.period_start), None)
    return next((p for p in periods if p.contains(inv.issue_date)), None)


def _analyze_plan(
    snap: Snapshot,
    plan: Plan,
    as_of: date,
    data_through: date,
    *,
    calendar: bool,
    full_period: bool,
    renewal: bool,
    up_to_date: bool,
) -> tuple[list[PeriodResult], list[Period], list[ResidueItem]]:
    all_periods = generate_periods(
        plan,
        snap.plans,
        calendar=calendar,
        full_period_on_amendment=full_period,
        auto_renewal=renewal,
        as_of=as_of,
    )
    results = {
        p.start: PeriodResult(plan.plan_id, p, "missing", Decimal(0), Decimal(0))
        for p in all_periods
        if p.start <= as_of
    }
    residue: list[ResidueItem] = []

    def memo_item(m: Memo, reason: str, detail: str, plan_id: str) -> ResidueItem:
        return ResidueItem(
            "memo", m.memo_id, reason, plan_id, None, m.amount, m.currency, m.issue_date,
            invoice_id=m.invoice_id, detail=detail,
        )  # fmt: skip

    for inv in snap.invoices:
        if inv.plan_id != plan.plan_id:
            continue
        memos = snap.memos_of(inv.invoice_id)

        def drop(reason: str, detail: str, inv=inv, memos=memos) -> None:
            residue.append(
                ResidueItem(
                    "invoice",
                    inv.invoice_id,
                    reason,
                    plan.plan_id,
                    inv.customer_name,
                    inv.amount,
                    inv.currency,
                    inv.issue_date,
                    memo_ids=[m.memo_id for m in memos],
                    detail=detail,
                )  # fmt: skip
            )
            residue.extend(
                memo_item(m, reason, f"follows {inv.invoice_id}", plan.plan_id) for m in memos
            )

        if inv.source == "data" and inv.issue_date > as_of:
            drop("after_as_of", f"issued {inv.issue_date}, after the cutoff {as_of}")
            continue
        try:
            conv = convert(snap.rates, inv.amount, inv.currency, plan.currency, inv.issue_date)
        except DomainError as e:
            drop("no_fx_rate", str(e))
            continue
        period = _find_period(all_periods, inv)
        if period is None:
            drop("outside_term", f"no period of {plan.plan_id} contains {inv.issue_date}")
            continue
        if period.start > as_of:
            drop("after_as_of", f"its period starts {period.start}, after the cutoff {as_of}")
            continue
        res = results[period.start]
        flags = []
        if inv.status == "unpaid":
            flags.append("unpaid")
        if inv.currency != plan.currency:
            flags.append("currency_mismatch")
        res.invoices.append(
            InvoiceLine(
                inv.invoice_id,
                inv.amount,
                inv.currency,
                conv.converted,
                inv.issue_date,
                inv.status,
                inv.source,
                (inv.issue_date - period.start).days,
                flags,
                conv if inv.currency != plan.currency else None,
            )  # fmt: skip
        )
        res.invoiced += conv.converted
        for m in memos:
            if m.source == "data" and m.issue_date > as_of:
                residue.append(
                    memo_item(
                        m, "after_as_of", f"issued {m.issue_date}, after {as_of}", plan.plan_id
                    )
                )
                continue
            try:
                mc = convert(snap.rates, m.amount, m.currency, plan.currency, m.issue_date)
            except DomainError as e:
                residue.append(memo_item(m, "no_fx_rate", str(e), plan.plan_id))
                continue
            res.memos.append(
                MemoLine(
                    m.memo_id,
                    m.invoice_id,
                    m.amount,
                    m.currency,
                    mc.converted,
                    m.source,
                    mc if m.currency != plan.currency else None,
                )  # fmt: skip
            )
            res.credited += mc.converted

    for res in results.values():
        if not res.invoices:
            res.status = (
                "no_data" if res.period.start > data_through and not up_to_date else "missing"
            )
        elif res.net == res.expected:
            res.status = "ok"
        else:
            res.status = "underbilled" if res.net < res.expected else "overbilled"
    return list(results.values()), all_periods, residue


def _signature(periods: list[PeriodResult], residue: list[ResidueItem]) -> tuple:
    return (
        tuple(
            (r.period.start, r.period.end_excl, r.expected, r.net, r.status)
            for r in periods
            if r.status != "no_data"
        ),
        tuple(sorted((x.id, x.reason) for x in residue)),
    )


def detect(
    snap: Snapshot, settings: AnalysisSettings, today: date, plan_id: str | None = None
) -> Detection:
    if plan_id is not None:
        snap.plan(plan_id)  # NotFoundError for an unknown plan
    as_of = settings.as_of(today)
    data_through = min(snap.data_through, as_of)
    eff = {
        "calendar": settings.effective("period_calendar") == "calendar",
        "full_period": settings.effective("amendment_proration") == "full_period",
        "renewal": settings.effective("auto_renewal"),
        "up_to_date": settings.effective("data_up_to_date"),
    }

    plan_results: dict[str, PlanResult] = {}
    all_periods: dict[str, list[Period]] = {}
    for p in snap.plans.values():
        periods, allp, residue = _analyze_plan(snap, p, as_of, data_through, **eff)
        plan_results[p.plan_id] = PlanResult(p, periods, residue)
        all_periods[p.plan_id] = allp

    # Invoices with no plan (or an unknown one), and memos with no invoice (D22, D19).
    global_residue: list[ResidueItem] = []
    known_invoices = {i.invoice_id for i in snap.invoices}
    for inv in snap.invoices:
        if inv.plan_id and inv.plan_id in snap.plans:
            continue
        memos = snap.memos_of(inv.invoice_id)
        candidates: list[str] = []
        if inv.source == "data" and inv.issue_date > as_of:
            reason, detail = "after_as_of", f"issued {inv.issue_date}, after the cutoff {as_of}"
        elif inv.dismissed:
            reason, detail = "dismissed", "dismissed by the user (sandbox)"
        else:
            reason = "no_plan"
            detail = "no plan_id" if not inv.plan_id else f"plan '{inv.plan_id}' does not exist"
            candidates = [
                pid
                for pid, plan in snap.plans.items()
                if plan.customer_name == inv.customer_name
                and any(p.contains(inv.issue_date) for p in all_periods[pid])
            ]
        global_residue.append(
            ResidueItem(
                "invoice",
                inv.invoice_id,
                reason,
                inv.plan_id,
                inv.customer_name,
                inv.amount,
                inv.currency,
                inv.issue_date,
                candidates,
                memo_ids=[m.memo_id for m in memos],
                detail=detail,
            )  # fmt: skip
        )
        for m in memos:
            global_residue.append(
                ResidueItem(
                    "memo",
                    m.memo_id,
                    reason,
                    m.plan_id,
                    None,
                    m.amount,
                    m.currency,
                    m.issue_date,
                    invoice_id=m.invoice_id,
                    detail=f"follows {inv.invoice_id}",
                )  # fmt: skip
            )
        if reason == "no_plan":
            for pid in candidates:
                for res in plan_results[pid].periods:
                    if res.period.contains(inv.issue_date) and res.status in FINDING_STATUSES:
                        res.underlying_status = res.status
                        res.status = "pending_decision"
                        res.pending_invoice_ids.append(inv.invoice_id)
    for m in snap.memos:
        if m.invoice_id not in known_invoices:
            global_residue.append(
                ResidueItem(
                    "memo",
                    m.memo_id,
                    "orphan_memo",
                    m.plan_id,
                    None,
                    m.amount,
                    m.currency,
                    m.issue_date,
                    invoice_id=m.invoice_id,
                    detail=f"invoice '{m.invoice_id}' does not exist",
                )  # fmt: skip
            )

    scope_ids = [plan_id] if plan_id else list(snap.plans)
    scoped = [plan_results[pid] for pid in scope_ids]
    for pr in scoped:
        _set_confidence(snap, pr, settings, as_of, data_through, eff)

    residue = [x for pr in scoped for x in pr.residue]
    for x in global_residue:
        if plan_id is None or x.plan_id == plan_id or plan_id in x.candidates:
            residue.append(x)
        elif x.kind == "memo" and x.invoice_id in {
            r.id for r in global_residue if plan_id in r.candidates
        }:
            residue.append(x)
    return Detection(
        scope=plan_id or "all",
        settings=settings,
        as_of=as_of,
        as_of_inferred=not settings.values.get("as_of"),
        data_through=data_through,
        data_up_to_date=bool(eff["up_to_date"]),
        plans=scoped,
        residue=residue,
    )


def _set_confidence(
    snap: Snapshot,
    pr: PlanResult,
    settings: AnalysisSettings,
    as_of: date,
    data_through: date,
    eff: dict,
) -> None:
    """Low when an unset assumption changes this plan's result (D20)."""
    base_periods, _, base_residue = _analyze_plan(snap, pr.plan, as_of, data_through, **eff)
    base_sig = _signature(base_periods, base_residue)
    knob = {"period_calendar": "calendar", "amendment_proration": "full_period",
            "auto_renewal": "renewal"}  # fmt: skip
    for key in ASSUMPTION_KEYS:
        if settings.is_fixed(key):
            continue
        valid = SPECS[key][1]
        for alt in valid:
            if alt == settings.effective(key):
                continue
            alt_eff = dict(eff)
            alt_eff[knob[key]] = alt == "calendar" or alt == "full_period" or alt is True
            periods, _, residue = _analyze_plan(snap, pr.plan, as_of, data_through, **alt_eff)
            if _signature(periods, residue) != base_sig:
                pr.low_because.append(key)
                break
    pr.confidence = "low" if pr.low_because else "high"
