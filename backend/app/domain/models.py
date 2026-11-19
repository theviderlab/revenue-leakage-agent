"""Immutable records of the billing data, as read from data/ plus the sandbox overlay (D6)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.domain.errors import NotFoundError


@dataclass(frozen=True)
class PlanVersion:
    effective_from: date
    total_value: Decimal
    cadence: str
    entitlements: tuple[str, ...]
    notes: str
    source: str  # "data" or "sandbox"
    action_id: str | None = None


@dataclass(frozen=True)
class Plan:
    plan_id: str
    customer_name: str
    currency: str
    start_date: date
    versions: tuple[PlanVersion, ...]  # ordered by effective_from; the first is the base record
    source: str
    amends: str | None = None
    replaced_by: str | None = None

    def version_at(self, d: date) -> PlanVersion:
        current = self.versions[0]
        for v in self.versions:
            if v.effective_from <= d:
                current = v
        return current

    @property
    def latest(self) -> PlanVersion:
        return self.versions[-1]


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    plan_id: str  # effective plan: the original, or the one a link points to ("" = none)
    original_plan_id: str
    customer_name: str
    issue_date: date
    due_date: date
    amount: Decimal
    currency: str
    status: str
    description: str
    source: str
    period_start: date | None = None  # only make-goods (D7)
    action_id: str | None = None
    dismissed: bool = False


@dataclass(frozen=True)
class Memo:
    memo_id: str
    plan_id: str
    invoice_id: str
    amount: Decimal
    currency: str
    issue_date: date
    reason: str
    source: str
    action_id: str | None = None


@dataclass(frozen=True)
class Rate:
    on_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass(frozen=True)
class Snapshot:
    plans: dict[str, Plan]
    invoices: tuple[Invoice, ...]
    memos: tuple[Memo, ...]
    rates: tuple[Rate, ...]
    data_through: date  # last issue_date in data/ only, never the overlay (gotchas: domain)
    links: dict[str, str] = field(default_factory=dict)

    def plan(self, plan_id: str) -> Plan:
        if plan_id not in self.plans:
            raise NotFoundError(
                f"plan '{plan_id}' not found; known plans: {', '.join(sorted(self.plans))}"
            )
        return self.plans[plan_id]

    def invoice(self, invoice_id: str) -> Invoice:
        for inv in self.invoices:
            if inv.invoice_id == invoice_id:
                return inv
        raise NotFoundError(f"invoice '{invoice_id}' not found")

    def memos_of(self, invoice_id: str) -> list[Memo]:
        return [m for m in self.memos if m.invoice_id == invoice_id]
