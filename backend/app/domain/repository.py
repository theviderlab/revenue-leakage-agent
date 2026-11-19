"""Repository of data/ (read-only) with the sandbox overlay (D6).

data/ is cached by file mtime. The sandbox is read on every snapshot: a write must be visible
to the next read (gotchas: sandbox), so nothing about it is cached.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.domain.models import Invoice, Memo, Plan, PlanVersion, Rate, Snapshot
from app.domain.sandbox import Sandbox
from app.domain.util import parse_date, to_decimal

_FILES = ("billing_plans", "invoices", "credit_memos", "exchange_rates")
_cache: dict[Path, tuple[tuple[int, ...], dict[str, list[dict]]]] = {}


def _load_data(data_dir: Path) -> dict[str, list[dict]]:
    data_dir = Path(data_dir)
    stamps = tuple((data_dir / f"{n}.json").stat().st_mtime_ns for n in _FILES)
    cached = _cache.get(data_dir)
    if cached and cached[0] == stamps:
        return cached[1]
    raw: dict[str, list[dict]] = {}
    for name in _FILES:
        with open(data_dir / f"{name}.json", encoding="utf-8") as f:
            raw[name] = json.load(f)
    _cache[data_dir] = (stamps, raw)
    return raw


def _plan_version(rec: dict, effective_from: date, source: str, action_id: str | None):
    return PlanVersion(
        effective_from=effective_from,
        total_value=to_decimal(rec["total_value"], "total_value"),
        cadence=rec["cadence"],
        entitlements=tuple(rec.get("entitlements", [])),
        notes=rec.get("notes", ""),
        source=source,
        action_id=action_id,
    )


def active(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("ledger_state", "active") == "active"]


def load_snapshot(data_dir: Path, state_dir: Path) -> Snapshot:
    raw = _load_data(data_dir)
    sandbox = Sandbox(state_dir)

    # Plans: data/ first; `amends` marks the plan that replaces another (PLN-2001-A1 -> PLN-2001).
    replaced_by: dict[str, str] = {}
    for rec in raw["billing_plans"]:
        if rec.get("amends"):
            replaced_by[rec["amends"]] = rec["plan_id"]
    versions: dict[str, list[PlanVersion]] = {}
    bases: dict[str, dict] = {}
    for rec in raw["billing_plans"]:
        start = parse_date(rec["start_date"], "start_date")
        bases[rec["plan_id"]] = rec
        versions[rec["plan_id"]] = [_plan_version(rec, start, "data", None)]
    for rec in active(sandbox.read("billing_plans")):
        if rec["plan_id"] in versions:
            versions[rec["plan_id"]].append(
                _plan_version(rec, parse_date(rec["effective_from"]), "sandbox", rec["action_id"])
            )
    plans: dict[str, Plan] = {}
    for pid, rec in bases.items():
        vs = versions[pid]
        # Stable: equal effective_from keeps ledger order, so the later amendment wins.
        vs.sort(key=lambda v: v.effective_from)
        plans[pid] = Plan(
            plan_id=pid,
            customer_name=rec["customer_name"],
            currency=rec["currency"],
            start_date=parse_date(rec["start_date"], "start_date"),
            versions=tuple(vs),
            source="data",
            amends=rec.get("amends"),
            replaced_by=replaced_by.get(pid),
        )

    # Links and dismissals live only in the audit log (D5, D22).
    links: dict[str, str] = {}
    dismissed: set[str] = set()
    for entry in sandbox.read("audit_log"):
        if entry.get("rolled_back_at"):
            continue
        if entry["type"] == "link_invoice":
            links[entry["payload"]["invoice_id"]] = entry["payload"]["plan_id"]
        elif entry["type"] == "dismiss_invoice":
            dismissed.add(entry["payload"]["invoice_id"])

    invoices: list[Invoice] = []

    def add_invoice(rec: dict, source: str) -> None:
        orig = rec.get("plan_id") or ""
        invoices.append(
            Invoice(
                invoice_id=rec["invoice_id"],
                plan_id=links.get(rec["invoice_id"], orig),
                original_plan_id=orig,
                customer_name=rec["customer_name"],
                issue_date=parse_date(rec["issue_date"], "issue_date"),
                due_date=parse_date(rec["due_date"], "due_date"),
                amount=to_decimal(rec["amount_invoiced"], "amount_invoiced"),
                currency=rec["currency"],
                status=rec["status"],
                description=rec.get("description", ""),
                source=source,
                period_start=parse_date(rec["period_start"]) if rec.get("period_start") else None,
                action_id=rec.get("action_id"),
                dismissed=rec["invoice_id"] in dismissed,
            )
        )

    for rec in raw["invoices"]:
        add_invoice(rec, "data")
    for rec in active(sandbox.read("invoices")):
        add_invoice(rec, "sandbox")

    def memo(r: dict, source: str) -> Memo:
        return Memo(
            memo_id=r["memo_id"],
            plan_id=r["plan_id"],
            invoice_id=r["invoice_id"],
            amount=to_decimal(r["amount"], "amount"),
            currency=r["currency"],
            issue_date=parse_date(r["issue_date"]),
            reason=r.get("reason", ""),
            source=source,
            action_id=r.get("action_id"),
        )

    memos = [memo(r, "data") for r in raw["credit_memos"]]
    memos += [memo(r, "sandbox") for r in active(sandbox.read("credit_memos"))]

    rates = tuple(
        Rate(parse_date(r["date"]), r["from_currency"], r["to_currency"], Decimal(str(r["rate"])))
        for r in raw["exchange_rates"]
    )
    data_dates = [parse_date(r["issue_date"]) for r in raw["invoices"]]
    data_dates += [parse_date(r["issue_date"]) for r in raw["credit_memos"]]
    return Snapshot(
        plans=plans,
        invoices=tuple(invoices),
        memos=tuple(memos),
        rates=rates,
        data_through=max(data_dates),
        links=links,
    )
