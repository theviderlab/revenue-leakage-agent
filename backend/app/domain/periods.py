"""Expected billing periods of a plan (D20, D26, D31).

Rules: 12-month term from `start_date`; a plan that another plan `amends` is cut where the
amendment starts; amount per period = total_value / periods in 12 months, with the plan version
in force at the start of the period; a partial period is prorated by whole months when the
stretch is whole months, by days otherwise; optional renewal repeats the term with the latest
version.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.models import Plan, PlanVersion
from app.domain.util import add_months, money

CADENCE_MONTHS = {"Monthly": 1, "Quarterly": 3, "Annual": 12}


@dataclass(frozen=True)
class Period:
    plan_id: str
    start: date
    end_excl: date
    expected: Decimal
    full_amount: Decimal
    ratio: Decimal
    cadence: str
    term_index: int
    truncated_by: str | None  # "term_end", "amendment" or None
    version_action_id: str | None

    @property
    def end(self) -> date:
        return self.end_excl - timedelta(days=1)

    def contains(self, d: date) -> bool:
        return self.start <= d < self.end_excl


def cadence_months(cadence: str) -> int:
    return CADENCE_MONTHS[cadence]


def calendar_floor(d: date, n: int) -> date:
    if n == 1:
        return d.replace(day=1)
    if n == 3:
        return date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
    return date(d.year, 1, 1)


def whole_months(a: date, b: date, limit: int) -> int | None:
    for m in range(1, limit + 1):
        if add_months(a, m) == b:
            return m
    return None


def proration_ratio(start: date, end: date, full_start: date, full_end: date, n: int) -> Decimal:
    if (start, end) == (full_start, full_end):
        return Decimal(1)
    m = whole_months(start, end, n)
    if m is not None:
        return Decimal(m) / Decimal(n)
    return Decimal((end - start).days) / Decimal((full_end - full_start).days)


def _segment(
    plan: Plan,
    v: PlanVersion,
    seg_start: date,
    seg_end: date,
    cut_label: str | None,
    term_index: int,
    calendar: bool,
    full_period_on_amendment: bool,
) -> list[Period]:
    n = cadence_months(v.cadence)
    full_amount = v.total_value / Decimal(12 // n)
    first = calendar_floor(seg_start, n) if calendar else seg_start
    out: list[Period] = []
    idx = 0
    while True:
        full_start = add_months(first, n * idx)
        full_end = add_months(first, n * (idx + 1))
        start = max(full_start, seg_start)
        if start >= seg_end:
            break
        end = min(full_end, seg_end)
        truncated_by = None
        if end < full_end:
            if cut_label == "amendment" and full_period_on_amendment:
                end = full_end
            else:
                truncated_by = cut_label
        ratio = proration_ratio(start, end, full_start, full_end, n)
        out.append(
            Period(
                plan_id=plan.plan_id,
                start=start,
                end_excl=end,
                expected=money(full_amount * ratio),
                full_amount=money(full_amount),
                ratio=ratio,
                cadence=v.cadence,
                term_index=term_index,
                truncated_by=truncated_by,
                version_action_id=v.action_id,
            )
        )
        idx += 1
    return out


def generate_periods(
    plan: Plan,
    plans: dict[str, Plan],
    *,
    calendar: bool,
    full_period_on_amendment: bool,
    auto_renewal: bool,
    as_of: date,
) -> list[Period]:
    natural_end = add_months(plan.start_date, 12)
    cut_end, cut_label = natural_end, "term_end"
    if plan.replaced_by and plan.replaced_by in plans:
        replacer_start = plans[plan.replaced_by].start_date
        if replacer_start < natural_end:
            cut_end, cut_label = replacer_start, "amendment"

    versions = [v for v in plan.versions if v.effective_from < cut_end]
    out: list[Period] = []
    for i, v in enumerate(versions):
        seg_end = versions[i + 1].effective_from if i + 1 < len(versions) else cut_end
        if v.effective_from >= seg_end:
            continue  # superseded by an amendment with the same effective_from
        out += _segment(
            plan,
            v,
            v.effective_from,
            seg_end,
            cut_label if seg_end == cut_end else None,
            0,
            calendar,
            full_period_on_amendment,
        )
    if auto_renewal and not plan.replaced_by:
        for k in range(1, 21):
            term_start = add_months(plan.start_date, 12 * k)
            if term_start > as_of:
                break
            out += _segment(
                plan,
                plan.latest,
                term_start,
                add_months(plan.start_date, 12 * (k + 1)),
                "term_end",
                k,
                calendar,
                full_period_on_amendment,
            )
    return out
