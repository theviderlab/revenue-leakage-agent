"""Corrective actions: proposals validated against the detector, apply and rollback (D7, D8, D13,
D15, D17, D22, D23, D25, D26, D34).

Evidence and `reason` are generated here from the detector's finding, never by the LLM. `apply`
revalidates by proposing again with the same arguments, so data, sandbox or settings that changed
between the proposal and the approval cannot slip through.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.domain.detector import Detection, PeriodResult
from app.domain.errors import DomainError, NotFoundError
from app.domain.models import Plan
from app.domain.periods import cadence_months, calendar_floor
from app.domain.sandbox import LOCK
from app.domain.util import add_months, cents_amount, money, num, parse_date

if TYPE_CHECKING:
    from app.domain.billing import Billing

MAKE_GOOD = "make_good_invoice"
CREDIT_MEMO = "credit_memo"
AMENDMENT = "plan_amendment"
LINK = "link_invoice"
DISMISS = "dismiss_invoice"
LEDGER_OF = {MAKE_GOOD: "invoices", CREDIT_MEMO: "credit_memos", AMENDMENT: "billing_plans"}

AMENDABLE = ("total_value", "cadence", "entitlements", "notes")
IDENTITY = ("plan_id", "customer_name", "start_date", "currency")
PAYMENT_TERM_DAYS = 15  # the payment term of PLN-1001 (D25)


@dataclass
class Proposal:
    type: str
    plan_id: str | None
    args: dict[str, Any]
    payload: dict[str, Any]
    evidence: list[str]
    reason: str
    settings_version: int
    affected_plans: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "plan_id": self.plan_id,
            "args": self.args,
            "payload": self.payload,
            "evidence": self.evidence,
            "reason": self.reason,
            "settings_version": self.settings_version,
            "affected_plans": self.affected_plans,
        }


def _fmt(d: Decimal) -> str:
    return f"{num(d):,}"


def _period_line(r: PeriodResult, ccy: str) -> str:
    return (
        f"{r.plan_id} {r.period.start} to {r.period.end}: expected {_fmt(r.expected)} {ccy}, "
        f"invoiced {_fmt(r.invoiced)}, credited {_fmt(r.credited)}, net {_fmt(r.net)}, "
        f"difference {_fmt(r.difference)} ({r.underlying_status or r.status})"
    )


def _assumption_line(det: Detection) -> str:
    return (
        f"Assumptions: cutoff {det.as_of} ({'inferred' if det.as_of_inferred else 'set'}), "
        f"data through {det.data_through}, billing in advance, settings version "
        f"{det.settings.version}"
    )


def _require_high(det: Detection, plan_id: str) -> None:
    pr = det.plan(plan_id)
    if pr.confidence != "high":
        raise DomainError(
            f"plan {plan_id} has low confidence: the result depends on settings that are not set "
            f"({', '.join(pr.low_because)}). Ask the user to set them in the panel; no "
            "make-good or credit memo is admitted until then."
        )


def _why_not(r: PeriodResult, wanted: str) -> DomainError:
    s = r.status
    detail = {
        "ok": "the period is billed correctly",
        "pending_decision": (
            f"invoice(s) {', '.join(r.pending_invoice_ids)} with no plan could belong to it: "
            "link or dismiss them first"
        ),
        "no_data": "the period starts after the end of the data, so there is no evidence",
        "overbilled": "the period is overbilled (only a credit memo applies)",
        "missing": "the period is missing (a make-good applies)",
        "underbilled": "the period is underbilled (a make-good applies)",
    }[s]
    return DomainError(
        f"{r.plan_id} period {r.period.start} is {s}, not admissible for a {wanted}: {detail}"
    )


# --- proposals ---------------------------------------------------------------------------


def propose_make_good(b: "Billing", plan_id: str, period_start: Any, amount: Any) -> Proposal:
    start = parse_date(period_start, "period_start")
    amt = b.positive(amount)
    det = b.detection(plan_id)
    plan = det.plan(plan_id).plan
    r = det.period(plan_id, start)
    if r is None:
        starts = ", ".join(x.period.start.isoformat() for x in det.plan(plan_id).periods)
        raise DomainError(f"{plan_id} has no expected period starting {start}; periods: {starts}")
    if r.status not in ("missing", "underbilled"):
        raise _why_not(r, "make-good")
    _require_high(det, plan_id)
    if amt > r.shortfall:
        raise DomainError(
            f"amount {_fmt(amt)} exceeds the shortfall {_fmt(r.shortfall)} of "
            f"{plan_id} {start}; a make-good is at most the shortfall"
        )
    ccy = plan.currency
    return Proposal(
        type=MAKE_GOOD,
        plan_id=plan_id,
        args={"plan_id": plan_id, "period_start": start.isoformat(), "amount": num(amt)},
        payload={
            "plan_id": plan_id,
            "customer_name": plan.customer_name,
            "period_start": start.isoformat(),
            "period_end": r.period.end.isoformat(),
            "amount_invoiced": num(amt),
            "currency": ccy,
            "status": "unpaid",
        },
        evidence=[
            _period_line(r, ccy),
            f"Shortfall {_fmt(r.shortfall)} {ccy}; make-good {_fmt(amt)} {ccy} "
            f"({'full' if amt == r.shortfall else 'partial'}, remaining after it: "
            f"{_fmt(r.shortfall - amt)} {ccy})",
            f"Plan confidence: {det.plan(plan_id).confidence}",
            _assumption_line(det),
        ],
        reason=(
            f"{'Missing' if r.status == 'missing' else 'Under-billed'} billing for {plan_id} "
            f"period {start} to {r.period.end}: expected {_fmt(r.expected)} {ccy}, net invoiced "
            f"{_fmt(r.net)} {ccy}; recover {_fmt(amt)} {ccy}."
        ),
        settings_version=det.settings.version,
        affected_plans=[plan_id],
    )


def propose_credit_memo(b: "Billing", invoice_id: str, amount: Any) -> Proposal:
    amt = b.positive(amount)
    snap = b.snapshot()
    inv = snap.invoice(invoice_id)
    if not inv.plan_id or inv.plan_id not in snap.plans:
        raise DomainError(f"invoice {invoice_id} has no plan: link it to a plan first")
    det = b.detection(inv.plan_id)
    r = det.period_of_invoice(invoice_id)
    if r is None:
        item = det.residue_of(invoice_id)
        raise DomainError(
            f"invoice {invoice_id} is not in any period of {inv.plan_id}"
            + (f" (unexplained: {item.reason}, {item.detail})" if item else "")
        )
    if r.status != "overbilled":
        raise _why_not(r, "credit memo")
    _require_high(det, inv.plan_id)
    if amt > r.excess:
        raise DomainError(
            f"amount {_fmt(amt)} exceeds the excess {_fmt(r.excess)} of {r.plan_id} "
            f"{r.period.start}; a credit memo is at most the excess"
        )
    line = next(x for x in r.invoices if x.invoice_id == invoice_id)
    already = sum(
        (m.amount_in_plan_currency for m in r.memos if m.invoice_id == invoice_id), Decimal(0)
    )
    if amt > line.amount_in_plan_currency - already:
        raise DomainError(
            f"amount {_fmt(amt)} exceeds what is left of invoice {invoice_id} "
            f"({_fmt(line.amount_in_plan_currency - already)})"
        )
    ccy = det.plan(inv.plan_id).plan.currency
    return Proposal(
        type=CREDIT_MEMO,
        plan_id=inv.plan_id,
        args={"invoice_id": invoice_id, "amount": num(amt)},
        payload={
            "plan_id": inv.plan_id,
            "invoice_id": invoice_id,
            "amount": num(amt),
            "currency": ccy,
        },  # fmt: skip
        evidence=[
            _period_line(r, ccy),
            f"Invoice {invoice_id}: {_fmt(inv.amount)} {inv.currency}"
            + (f" = {_fmt(line.amount_in_plan_currency)} {ccy}" if inv.currency != ccy else ""),
            f"Excess {_fmt(r.excess)} {ccy}; credit memo {_fmt(amt)} {ccy}",
            f"Plan confidence: {det.plan(inv.plan_id).confidence}",
            _assumption_line(det),
        ],
        reason=(
            f"Over-billing of {inv.plan_id} period {r.period.start} to {r.period.end}: expected "
            f"{_fmt(r.expected)} {ccy}, net invoiced {_fmt(r.net)} {ccy}; credit {_fmt(amt)} "
            f"{ccy} on invoice {invoice_id}."
        ),
        settings_version=det.settings.version,
        affected_plans=[inv.plan_id],
    )


def _boundaries(plan: Plan, cadence: str, calendar: bool) -> set[date]:
    n = cadence_months(cadence)
    out = set()
    end = add_months(plan.start_date, 12)
    first = calendar_floor(plan.start_date, n) if calendar else plan.start_date
    k = 0
    while True:
        d = add_months(first, n * k)
        if d >= end:
            return out
        if d >= plan.start_date:
            out.add(d)
        k += 1


def _validate_change_set(change_set: Any) -> dict[str, Any]:
    if not isinstance(change_set, dict) or not change_set:
        raise DomainError("change_set must be a non-empty object")
    identity = [k for k in change_set if k in IDENTITY]
    if identity:
        raise DomainError(
            f"identity fields cannot be amended ({', '.join(identity)}): that is another "
            "contract, not an amendment"
        )
    unknown = [k for k in change_set if k not in AMENDABLE]
    if unknown:
        raise DomainError(
            f"cannot amend {', '.join(unknown)}; amendable fields: {', '.join(AMENDABLE)}"
        )
    out: dict[str, Any] = {}
    for k, v in change_set.items():
        if k == "total_value":
            d = cents_amount(v, "total_value")
            if d <= 0:
                raise DomainError("total_value must be greater than 0")
            out[k] = num(d)
        elif k == "cadence":
            if v not in ("Monthly", "Quarterly", "Annual"):
                raise DomainError("cadence must be Monthly, Quarterly or Annual")
            out[k] = v
        elif k == "entitlements":
            if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
                raise DomainError("entitlements must be a non-empty list of strings")
            out[k] = list(v)
        else:
            if not isinstance(v, str):
                raise DomainError("notes must be a string")
            out[k] = v
    return out


def propose_plan_amendment(
    b: "Billing", plan_id: str, effective_from: Any, change_set: Any
) -> Proposal:
    snap = b.snapshot()
    plan = snap.plan(plan_id)
    if plan.replaced_by:
        raise DomainError(
            f"{plan_id} is replaced by {plan.replaced_by} (amends); amend {plan.replaced_by}"
        )
    changes = _validate_change_set(change_set)
    eff = parse_date(effective_from, "effective_from")
    cur = plan.latest
    current = {
        "total_value": num(cur.total_value),
        "cadence": cur.cadence,
        "entitlements": list(cur.entitlements),
        "notes": cur.notes,
    }
    if all(current[k] == v for k, v in changes.items()):
        raise DomainError("the change_set does not change anything in the current version")
    term_end = add_months(plan.start_date, 12)
    if not (plan.start_date <= eff < term_end):
        raise DomainError(
            f"effective_from {eff} is outside the term of {plan_id} ({plan.start_date} to "
            f"{term_end - timedelta(days=1)})"
        )
    if eff < cur.effective_from:
        raise DomainError(
            f"effective_from {eff} is before the current version ({cur.effective_from}"
            f"{', ' + cur.action_id if cur.action_id else ''}); roll that amendment back first"
        )
    calendar = b.settings().effective("period_calendar") == "calendar"
    new_cadence = changes.get("cadence", cur.cadence)
    for label, cadence in (("current", cur.cadence), ("new", new_cadence)):
        ok = _boundaries(plan, cadence, calendar)
        if eff not in ok:
            starts = ", ".join(sorted(d.isoformat() for d in ok))
            raise DomainError(
                f"effective_from {eff} is not the start of a period with the {label} cadence "
                f"({cadence}); valid starts: {starts}"
            )
    new_total = Decimal(str(changes.get("total_value", current["total_value"])))
    per_period = money(new_total / Decimal(12 // cadence_months(new_cadence)))
    payload = {
        "plan_id": plan_id,
        "customer_name": plan.customer_name,
        "currency": plan.currency,
        "start_date": plan.start_date.isoformat(),
        **current,
        **changes,
        "effective_from": eff.isoformat(),
    }
    if plan.amends:
        payload["amends"] = plan.amends
    det = b.detection(plan_id)
    return Proposal(
        type=AMENDMENT,
        plan_id=plan_id,
        args={"plan_id": plan_id, "effective_from": eff.isoformat(), "change_set": changes},
        payload=payload,
        evidence=[
            f"Current version ({cur.effective_from}): "
            + ", ".join(f"{k}={current[k]}" for k in changes),
            "New version: " + ", ".join(f"{k}={v}" for k, v in changes.items()),
            f"From {eff}: expected {_fmt(per_period)} {plan.currency} per {new_cadence} period",
            f"Plan {plan_id} keeps its id: invoices and credit memos still point to it",
            _assumption_line(det),
        ],
        reason=(
            f"Amend {plan_id} from {eff}: "
            + ", ".join(f"{k} {current[k]} -> {v}" for k, v in changes.items())
            + "."
        ),
        settings_version=det.settings.version,
        affected_plans=[plan_id],
    )


def _unmatched(b: "Billing", invoice_id: str, wanted: str) -> tuple[Detection, Any]:
    det = b.detection()
    b.snapshot().invoice(invoice_id)
    item = det.residue_of(invoice_id)
    if item is None:
        raise DomainError(
            f"invoice {invoice_id} has a plan; only invoices with no plan can be {wanted}"
        )
    if item.reason != "no_plan":
        raise DomainError(
            f"invoice {invoice_id} is unexplained for another reason ({item.reason}: "
            f"{item.detail}); only 'no_plan' can be {wanted}"
        )
    return det, item


def propose_link_invoice(b: "Billing", invoice_id: str, plan_id: str) -> Proposal:
    det, item = _unmatched(b, invoice_id, "linked")
    if plan_id not in item.candidates:
        raise DomainError(
            f"{plan_id} is not a candidate for {invoice_id}; candidates (same customer, term "
            f"contains {item.issue_date}): {', '.join(item.candidates) or 'none'}"
        )
    snap = b.snapshot()
    inv = snap.invoice(invoice_id)
    plan = snap.plan(plan_id)
    evidence = [
        f"Invoice {invoice_id}: {_fmt(inv.amount)} {inv.currency}, {inv.customer_name}, issued "
        f"{inv.issue_date}, no plan_id ({inv.description})",
        f"Candidates: {', '.join(item.candidates)}",
    ]
    r = next((x for x in det.plan(plan_id).periods if x.period.contains(inv.issue_date)), None)
    if r is not None:
        evidence.append(f"Period before linking: {_period_line(r, plan.currency)}")
        new_net = r.net + inv.amount if inv.currency == plan.currency else None
        if new_net is not None:
            evidence.append(
                f"After linking: net {_fmt(new_net)} {plan.currency} vs expected "
                f"{_fmt(r.expected)} (difference {_fmt(new_net - r.expected)})"
            )
    evidence.append(_assumption_line(det))
    return Proposal(
        type=LINK,
        plan_id=plan_id,
        args={"invoice_id": invoice_id, "plan_id": plan_id},
        payload={"invoice_id": invoice_id, "plan_id": plan_id, "candidates": item.candidates},
        evidence=evidence,
        reason=f"Link invoice {invoice_id} (no plan_id) to {plan_id}, the plan it belongs to.",
        settings_version=det.settings.version,
        affected_plans=list(item.candidates),
    )


def propose_dismiss_invoice(b: "Billing", invoice_id: str) -> Proposal:
    det, item = _unmatched(b, invoice_id, "dismissed")
    inv = b.snapshot().invoice(invoice_id)
    return Proposal(
        type=DISMISS,
        plan_id=None,
        args={"invoice_id": invoice_id},
        payload={"invoice_id": invoice_id, "candidates": item.candidates},
        evidence=[
            f"Invoice {invoice_id}: {_fmt(inv.amount)} {inv.currency}, {inv.customer_name}, "
            f"issued {inv.issue_date}, no plan_id ({inv.description})",
            f"Candidates: {', '.join(item.candidates) or 'none'}",
            _assumption_line(det),
        ],
        reason=f"Dismiss invoice {invoice_id}: it does not belong to any plan; it stays listed as "
        "unexplained and stops blocking the periods it could have covered.",
        settings_version=det.settings.version,
        affected_plans=list(item.candidates),
    )


def revalidate(b: "Billing", proposal: dict) -> Proposal:
    """Propose again with the same arguments (D17): raises DomainError if it no longer holds."""
    t, a = proposal["type"], proposal["args"]
    if t == MAKE_GOOD:
        return propose_make_good(b, a["plan_id"], a["period_start"], a["amount"])
    if t == CREDIT_MEMO:
        return propose_credit_memo(b, a["invoice_id"], a["amount"])
    if t == AMENDMENT:
        return propose_plan_amendment(b, a["plan_id"], a["effective_from"], a["change_set"])
    if t == LINK:
        return propose_link_invoice(b, a["invoice_id"], a["plan_id"])
    if t == DISMISS:
        return propose_dismiss_invoice(b, a["invoice_id"])
    raise DomainError(f"unknown proposal type '{t}'")


# --- apply and rollback --------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def apply_proposal(
    b: "Billing", proposal: dict, *, proposal_id: str, thread_id: str | None
) -> dict:
    """Revalidate and write the ledger record and the audit entry; returns the audit entry."""
    with LOCK:
        fresh = revalidate(b, proposal)
        today = b.today()
        sb = b.sandbox
        action_id = sb.next_id(fresh.type)
        common = {"action_id": action_id, "ledger_state": "active", "source": "sandbox"}
        p = fresh.payload
        if fresh.type == MAKE_GOOD:
            issue = today
            record = {
                "invoice_id": action_id,
                "plan_id": p["plan_id"],
                "customer_name": p["customer_name"],
                "issue_date": issue.isoformat(),
                "due_date": (issue + timedelta(days=PAYMENT_TERM_DAYS)).isoformat(),
                "amount_invoiced": p["amount_invoiced"],
                "currency": p["currency"],
                "status": "unpaid",
                "description": (
                    f"Make-good invoice for {p['plan_id']} {p['period_start']} to {p['period_end']}"
                ),
                "period_start": p["period_start"],
                **common,
            }
        elif fresh.type == CREDIT_MEMO:
            record = {
                "memo_id": action_id,
                "plan_id": p["plan_id"],
                "invoice_id": p["invoice_id"],
                "amount": p["amount"],
                "currency": p["currency"],
                "issue_date": today.isoformat(),
                "reason": fresh.reason,
                **common,
            }
        elif fresh.type == AMENDMENT:
            record = {**{k: v for k, v in p.items()}, **common}
        else:
            record = None
        if record is not None:
            ledger = LEDGER_OF[fresh.type]
            sb.write(ledger, [*sb.read(ledger), record])
        entry = {
            "action_id": action_id,
            "type": fresh.type,
            "proposal_id": proposal_id,
            "thread_id": thread_id,
            "plan_id": fresh.plan_id,
            "payload": record if record is not None else fresh.payload,
            "evidence": fresh.evidence,
            "reason": fresh.reason,
            "applied_at": _now(),
            "rolled_back_at": None,
        }
        sb.write("audit_log", [*sb.read("audit_log"), entry])
        return entry


def last_active_action(b: "Billing") -> dict | None:
    active = [e for e in b.sandbox.read("audit_log") if not e.get("rolled_back_at")]
    return active[-1] if active else None


def check_rollback(b: "Billing", action_id: str) -> dict:
    """D8: only the last active action can be rolled back. Returns its audit entry."""
    audit = b.sandbox.read("audit_log")
    entry = next((e for e in audit if e["action_id"] == action_id), None)
    if entry is None:
        raise NotFoundError(f"action '{action_id}' not found in the audit log")
    if entry.get("rolled_back_at"):
        raise DomainError(f"action {action_id} was already rolled back")
    last = last_active_action(b)
    if last is None or last["action_id"] != action_id:
        raise DomainError(
            f"only the last active action can be rolled back; it is {last['action_id']}"
            f" ({last['type']}), roll it back first"
            if last
            else "there is no active action"
        )
    return entry


def rollback_action(b: "Billing", action_id: str) -> dict:
    """Mark the action and its ledger records as rolled back; nothing is deleted (D5)."""
    with LOCK:
        check_rollback(b, action_id)
        sb = b.sandbox
        now = _now()
        audit = sb.read("audit_log")
        entry = next(e for e in audit if e["action_id"] == action_id)
        ledger = LEDGER_OF.get(entry["type"])
        if ledger:
            rows = sb.read(ledger)
            for row in rows:
                if row.get("action_id") == action_id:
                    row["ledger_state"] = "rolled_back"
            sb.write(ledger, rows)
        entry["rolled_back_at"] = now
        # The audit entry keeps a copy of the record it wrote: keep it consistent.
        if isinstance(entry.get("payload"), dict) and "ledger_state" in entry["payload"]:
            entry["payload"]["ledger_state"] = "rolled_back"
        sb.write("audit_log", audit)
        return entry


def affected_plans(entry: dict) -> list[str]:
    """Plans to recompute after an action (D23); links and dismissals touch the candidates."""
    if entry["type"] in (LINK, DISMISS):
        return list(entry["payload"].get("candidates", []))
    return [entry["plan_id"]] if entry.get("plan_id") else []


def list_actions(b: "Billing") -> list[dict]:
    return b.sandbox.read("audit_log")
