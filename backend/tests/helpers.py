from datetime import date

from app.domain.billing import Billing
from tests.conftest import DATA_DIR


def diffs(detection) -> dict[str, float]:
    return {p.plan.plan_id: float(p.totals["difference"]) for p in detection.plans}


def billing_at(tmp_path, today: date) -> Billing:
    return Billing(DATA_DIR, tmp_path / "state", clock=lambda: today)


def statuses(detection, plan_id: str) -> list[tuple[str, str, float]]:
    p = detection.plan(plan_id)
    return [(r.period.start.isoformat(), r.status, float(r.difference)) for r in p.periods]
