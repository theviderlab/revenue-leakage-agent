"""Domain objects -> the JSON shapes of the API contract (app/schemas.py).

Findings and proposals are stored in the graph state in these shapes, so the API can return the
state without recomputing anything (and `stale` is a comparison of settings versions, D24).
"""

from typing import Any

from app.domain.actions import Proposal
from app.domain.detector import Detection, PlanResult, ResidueItem
from app.domain.util import num


def _flags(invoices) -> list[str]:
    seen: list[str] = []
    for line in invoices:
        for f in line.flags:
            if f not in seen:
                seen.append(f)
    return seen


def plan_findings(pr: PlanResult, settings_version: int) -> dict:
    periods = []
    for r in pr.periods:
        periods.append(
            {
                "period_start": r.period.start.isoformat(),
                "period_end": r.period.end.isoformat(),
                "status": r.status,
                "expected": num(r.expected),
                "invoiced": num(r.invoiced),
                "credited": num(r.credited),
                "net": num(r.net),
                "diff": num(r.difference),
                "invoice_ids": [i.invoice_id for i in r.invoices],
                "memo_ids": [m.memo_id for m in r.memos],
                "flags": _flags(r.invoices),
            }
        )
    t = pr.totals
    return {
        "plan_id": pr.plan.plan_id,
        "confidence": pr.confidence,
        "confidence_reasons": list(pr.low_because),
        "periods": periods,
        "totals": {
            "expected": num(t["expected"]),
            "net": num(t["net"]),
            "diff": num(t["difference"]),
        },
        "settings_version": settings_version,
        "stale": False,
    }


def unexplained(x: ResidueItem) -> dict:
    return {
        "record_id": x.id,
        "kind": x.kind,
        "reason": x.reason,
        "amount": num(x.amount),
        "currency": x.currency,
        "candidates": list(x.candidates),
    }


def assumption_lines(det: Detection) -> list[str]:
    a = det.assumptions
    lines = [
        "Billing is in advance: each invoice belongs to the period containing its issue date",
        f"Cutoff {a['as_of']} "
        f"({'inferred: today' if a['as_of_inferred'] else 'set in the panel'}); "
        f"data through {a['data_through']}",
    ]
    for s in a["settings"]:
        if s["key"] == "as_of":
            continue
        lines.append(f"{s['key']} = {s['value']} ({'set' if s['fixed'] else 'default, not set'})")
    return lines


def findings_payload(det: Detection) -> dict:
    t = det.totals
    return {
        "plans": [plan_findings(pr, det.settings.version) for pr in det.plans],
        "totals": {"expected": t["expected"], "net": t["net"], "diff": t["difference"]},
        "assumptions": assumption_lines(det),
        "unexplained": [unexplained(x) for x in det.residue],
    }


def proposal_dict(proposal_id: str, p: Proposal, status: str = "pending") -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "type": p.type,
        "plan_id": p.plan_id,
        "args": p.args,
        "evidence": p.evidence,
        "reason": p.reason,
        "settings_version": p.settings_version,
        "status": status,
    }
