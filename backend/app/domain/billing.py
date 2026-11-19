"""Facade of the billing domain: the read tools of D27 and (actions.py) the sandbox actions.

Each call reads a fresh snapshot (data/ cached by mtime, sandbox never cached), so a write is
visible to the next read (D6, gotchas: sandbox). The clock is injected: tests use fixed dates.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.domain import actions
from app.domain.analysis_settings import SPECS, AnalysisSettings, SettingsStore
from app.domain.detector import Detection, detect
from app.domain.errors import DomainError
from app.domain.fx import convert
from app.domain.knowledge import get_concept
from app.domain.models import Plan, Snapshot
from app.domain.repository import load_snapshot
from app.domain.sandbox import Sandbox
from app.domain.util import add_months, num, parse_date, to_decimal


def plan_version_dict(v) -> dict:
    return {
        "effective_from": v.effective_from.isoformat(),
        "total_value": num(v.total_value),
        "cadence": v.cadence,
        "entitlements": list(v.entitlements),
        "notes": v.notes,
        "source": v.source,
        "action_id": v.action_id,
    }


def plan_summary(p: Plan) -> dict:
    return {
        "plan_id": p.plan_id,
        "customer_name": p.customer_name,
        "currency": p.currency,
        "cadence": p.latest.cadence,
        "total_value": num(p.latest.total_value),
        "start_date": p.start_date.isoformat(),
        "term_end": (add_months(p.start_date, 12)).isoformat(),
        "amends": p.amends,
        "replaced_by": p.replaced_by,
        "entitlements": list(p.latest.entitlements),
        "versions": len(p.versions),
        "source": "sandbox" if any(v.source == "sandbox" for v in p.versions) else p.source,
    }


class Billing:
    def __init__(self, data_dir: Path, state_dir: Path, clock: Callable[[], date] = date.today):
        self.data_dir = Path(data_dir)
        self.state_dir = Path(state_dir)
        self.clock = clock
        self.sandbox = Sandbox(self.state_dir)
        self.store = SettingsStore(self.state_dir)

    # --- plumbing -------------------------------------------------------------------------
    def today(self) -> date:
        return self.clock()

    def snapshot(self) -> Snapshot:
        return load_snapshot(self.data_dir, self.state_dir)

    def settings(self) -> AnalysisSettings:
        return self.store.get()

    def detection(self, plan_id: str | None = None) -> Detection:
        return detect(self.snapshot(), self.settings(), self.today(), plan_id)

    # --- settings (D24) -------------------------------------------------------------------
    def describe_settings(self) -> dict:
        s = self.settings()
        snap = self.snapshot()
        today = self.today()
        return {
            "version": s.version,
            "data_through": min(snap.data_through, s.as_of(today)).isoformat(),
            "settings": {
                key: {"value": s.values.get(key), "default": SPECS[key][0], "valid": SPECS[key][1]}
                for key in SPECS
            },
        }

    def update_settings(self, changes: dict[str, Any]) -> AnalysisSettings:
        return self.store.update(changes, self.today())

    # --- read tools (D27) -----------------------------------------------------------------
    def list_plans(self) -> list[dict]:
        return [plan_summary(p) for p in self.snapshot().plans.values()]

    def load_plan(self, plan_id: str) -> dict:
        p = self.snapshot().plan(plan_id)
        return {**plan_summary(p), "versions": [plan_version_dict(v) for v in p.versions]}

    def query_invoices(
        self,
        plan_id: str | None = None,
        customer_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        snap = self.snapshot()
        lo = parse_date(date_from, "date_from") if date_from else None
        hi = parse_date(date_to, "date_to") if date_to else None
        out = []
        for inv in snap.invoices:
            if plan_id is not None and inv.plan_id != plan_id:
                continue
            if customer_name is not None and inv.customer_name.lower() != customer_name.lower():
                continue
            if (lo and inv.issue_date < lo) or (hi and inv.issue_date > hi):
                continue
            out.append(
                {
                    "invoice_id": inv.invoice_id,
                    "plan_id": inv.plan_id or None,
                    "original_plan_id": inv.original_plan_id or None,
                    "customer_name": inv.customer_name,
                    "issue_date": inv.issue_date.isoformat(),
                    "due_date": inv.due_date.isoformat(),
                    "amount_invoiced": num(inv.amount),
                    "currency": inv.currency,
                    "status": inv.status,
                    "description": inv.description,
                    "period_start": inv.period_start.isoformat() if inv.period_start else None,
                    "dismissed": inv.dismissed,
                    "source": inv.source,
                    "action_id": inv.action_id,
                    "credit_memos": [
                        {
                            "memo_id": m.memo_id,
                            "amount": num(m.amount),
                            "currency": m.currency,
                            "issue_date": m.issue_date.isoformat(),
                            "reason": m.reason,
                            "source": m.source,
                        }
                        for m in snap.memos_of(inv.invoice_id)
                    ],
                }
            )
        return out

    def fx_convert(self, amount: Any, from_ccy: str, to_ccy: str, on_date: str) -> dict:
        c = convert(
            self.snapshot().rates,
            to_decimal(amount),
            from_ccy,
            to_ccy,
            parse_date(on_date, "on_date"),
        )
        return c.to_dict()

    def detect_anomalies(self, plan_id: str | None = None) -> Detection:
        return self.detection(plan_id)

    def get_concept(self, key: str) -> dict:
        return get_concept(key)

    # --- helpers for the actions ----------------------------------------------------------
    @staticmethod
    def positive(amount: Any) -> Decimal:
        from app.domain.util import cents_amount

        d = cents_amount(amount)
        if d <= 0:
            raise DomainError("amount must be greater than 0")
        return d

    # --- actions (D15, D17, D34) ----------------------------------------------------------
    def propose_make_good_invoice(self, plan_id: str, period_start: Any, amount: Any):
        return actions.propose_make_good(self, plan_id, period_start, amount)

    def propose_credit_memo(self, invoice_id: str, amount: Any):
        return actions.propose_credit_memo(self, invoice_id, amount)

    def propose_plan_amendment(self, plan_id: str, effective_from: Any, change_set: Any):
        return actions.propose_plan_amendment(self, plan_id, effective_from, change_set)

    def propose_link_invoice(self, invoice_id: str, plan_id: str):
        return actions.propose_link_invoice(self, invoice_id, plan_id)

    def propose_dismiss_invoice(self, invoice_id: str):
        return actions.propose_dismiss_invoice(self, invoice_id)

    def validate_proposal(self, proposal: dict):
        """Revalidate against the current detector; raises DomainError if it no longer holds."""
        return actions.revalidate(self, proposal)

    def apply(self, proposal: dict, *, proposal_id: str, thread_id: str | None = None) -> dict:
        return actions.apply_proposal(self, proposal, proposal_id=proposal_id, thread_id=thread_id)

    def check_rollback(self, action_id: str) -> dict:
        return actions.check_rollback(self, action_id)

    def rollback(self, action_id: str) -> dict:
        return actions.rollback_action(self, action_id)

    def list_actions(self) -> list[dict]:
        return actions.list_actions(self)

    @staticmethod
    def affected_plans(entry: dict) -> list[str]:
        return actions.affected_plans(entry)
