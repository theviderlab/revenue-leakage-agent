"""Sandbox actions: proposals validated against the detector, apply, rollback, overlay."""

import json
import shutil
from datetime import date

import pytest

from app.domain.billing import Billing
from app.domain.errors import DomainError, NotFoundError
from tests.conftest import DATA_DIR, DATA_THROUGH
from tests.helpers import diffs, statuses


def apply(billing, proposal, n=1):
    return billing.apply(proposal.to_dict(), proposal_id=f"PRP-{n:03d}", thread_id="t1")


def feb_make_good(billing, amount=7000):
    return billing.propose_make_good_invoice("PLN-1001", "2026-02-01", amount)


def low_overbilled_plan(tmp_path) -> Billing:
    """M33: PLN-3001 overbilled and low, on a copy of data/ with INV-3001 raised to 130,000."""
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    path = data / "invoices.json"
    rows = json.loads(path.read_text("utf-8"))
    for row in rows:
        if row["invoice_id"] == "INV-3001":
            row["amount_invoiced"] = 130000
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return Billing(data, tmp_path / "state", clock=lambda: DATA_THROUGH)


def test_make_good_full_then_rollback(billing):
    prop = feb_make_good(billing)
    assert prop.affected_plans == ["PLN-1001"] and prop.settings_version == 0
    assert any("Shortfall 7,000 USD" in e for e in prop.evidence)
    assert "2026-02-01" in prop.reason
    entry = apply(billing, prop)
    assert entry["action_id"] == "INV-MG-001" and entry["rolled_back_at"] is None
    assert entry["payload"]["issue_date"] == "2026-06-30"
    assert entry["payload"]["due_date"] == "2026-07-15"
    assert entry["payload"]["source"] == "sandbox" and entry["payload"]["ledger_state"] == "active"

    d = billing.detect_anomalies()
    assert diffs(d)["PLN-1001"] == 1500 and float(d.totals["difference"]) == -57228.26
    feb = d.period("PLN-1001", date(2026, 2, 1))
    assert feb.status == "ok" and feb.invoices[0].source == "sandbox"
    assert feb.invoices[0].invoice_id == "INV-MG-001"
    # data_through is never moved by sandbox records (gotchas: domain)
    assert d.data_through == date(2026, 6, 30)
    inv = {i["invoice_id"]: i for i in billing.query_invoices(plan_id="PLN-1001")}
    assert inv["INV-MG-001"]["source"] == "sandbox" and inv["INV-1001"]["source"] == "data"

    rolled = billing.rollback("INV-MG-001")
    assert rolled["rolled_back_at"]
    # The copy of the record in the audit entry says it is rolled back too
    assert rolled["payload"]["ledger_state"] == "rolled_back"
    on_disk = json.loads((billing.state_dir / "sandbox" / "audit_log.json").read_text("utf-8"))
    assert on_disk[0]["payload"]["ledger_state"] == "rolled_back"
    assert diffs(billing.detect_anomalies())["PLN-1001"] == -5500
    # append-only: the record is still in the ledger, marked
    ledger = json.loads((billing.state_dir / "sandbox" / "invoices.json").read_text("utf-8"))
    assert [(r["invoice_id"], r["ledger_state"]) for r in ledger] == [("INV-MG-001", "rolled_back")]
    # ids are never reused, even after a rollback
    assert apply(billing, feb_make_good(billing), 2)["action_id"] == "INV-MG-002"


def test_partial_make_good_and_its_remainder(billing):
    apply(billing, feb_make_good(billing, 3000))
    feb = billing.detect_anomalies().period("PLN-1001", date(2026, 2, 1))
    assert (feb.status, float(feb.shortfall)) == ("underbilled", 4000)
    with pytest.raises(DomainError, match="exceeds the shortfall"):
        feb_make_good(billing, 5000.01)
    apply(billing, feb_make_good(billing, 4000), 2)
    assert billing.detect_anomalies().period("PLN-1001", date(2026, 2, 1)).status == "ok"


def test_make_good_rejections(billing):
    with pytest.raises(DomainError, match="exceeds the shortfall"):
        feb_make_good(billing, 7000.01)
    for bad in (0, -5, "abc", 10.123):
        with pytest.raises(DomainError):
            feb_make_good(billing, bad)
    with pytest.raises(DomainError, match="is ok"):  # a correct period
        billing.propose_make_good_invoice("PLN-1001", "2026-01-01", 100)
    with pytest.raises(DomainError, match="no expected period"):
        billing.propose_make_good_invoice("PLN-1001", "2026-02-02", 100)
    with pytest.raises(DomainError, match="low confidence"):  # unset assumptions (M33)
        billing.propose_make_good_invoice("PLN-3001", "2025-07-15", 100)
    with pytest.raises(DomainError, match="pending_decision"):  # waits for INV-4001
        billing.propose_make_good_invoice("PLN-2001-A1", "2025-10-01", 100)
    with pytest.raises(NotFoundError):
        billing.propose_make_good_invoice("PLN-9999", "2026-02-01", 1)


def test_applied_twice_is_rejected(billing):
    prop = feb_make_good(billing)
    apply(billing, prop)
    with pytest.raises(DomainError):
        apply(billing, prop, 2)
    assert len(billing.list_actions()) == 1


def test_apply_revalidates_stale_proposals(billing):
    prop = billing.propose_dismiss_invoice("INV-4001")
    apply(billing, billing.propose_link_invoice("INV-4001", "PLN-2001-A1"))
    with pytest.raises(DomainError, match="only invoices with no plan"):
        apply(billing, prop, 2)


def test_rollback_only_the_last_active_action(billing):
    apply(billing, feb_make_good(billing, 3000))
    apply(billing, feb_make_good(billing, 4000), 2)
    with pytest.raises(DomainError, match="INV-MG-002"):
        billing.rollback("INV-MG-001")
    with pytest.raises(DomainError, match="INV-MG-002"):
        billing.check_rollback("INV-MG-001")
    billing.rollback("INV-MG-002")
    with pytest.raises(DomainError, match="already rolled back"):
        billing.rollback("INV-MG-002")
    billing.rollback("INV-MG-001")  # now it is the last active one
    with pytest.raises(NotFoundError):
        billing.rollback("INV-MG-099")
    assert billing.detect_anomalies().plan("PLN-1001").totals["difference"] == -5500


def test_link_invoice(billing):
    prop = billing.propose_link_invoice("INV-4001", "PLN-2001-A1")
    assert any("After linking" in e and "difference -13,000" in e for e in prop.evidence)
    entry = apply(billing, prop)
    assert entry["action_id"] == "LNK-001" and entry["payload"]["plan_id"] == "PLN-2001-A1"
    d = billing.detect_anomalies()
    assert diffs(d)["PLN-2001-A1"] == -13000 and float(d.totals["difference"]) == -49228.26
    assert statuses(d, "PLN-2001-A1")[1] == ("2025-10-01", "underbilled", -13000.0)
    assert [x.id for x in d.residue] == ["INV-1003"]
    # the make-good for the rest is now admitted, up to the remaining 13,000
    billing.propose_make_good_invoice("PLN-2001-A1", "2025-10-01", 13000)
    with pytest.raises(DomainError, match="shortfall"):
        billing.propose_make_good_invoice("PLN-2001-A1", "2025-10-01", 13000.01)
    billing.rollback("LNK-001")
    d = billing.detect_anomalies()
    assert float(d.totals["difference"]) == -64228.26
    assert [x.id for x in d.residue] == ["INV-1003", "INV-4001"]
    # no ledger file for links and dismissals (D5): only the audit log
    assert not (billing.state_dir / "sandbox" / "invoices.json").exists()


def test_link_rejections(billing):
    with pytest.raises(DomainError, match="not a candidate"):
        billing.propose_link_invoice("INV-4001", "PLN-2001")
    with pytest.raises(DomainError, match="only invoices with no plan"):
        billing.propose_link_invoice("INV-1001", "PLN-1001")
    with pytest.raises(NotFoundError):
        billing.propose_link_invoice("INV-0000", "PLN-1001")


def test_dismiss_invoice(billing):
    entry = apply(billing, billing.propose_dismiss_invoice("INV-4001"))
    assert entry["action_id"] == "DSM-001"
    d = billing.detect_anomalies()
    assert float(d.totals["difference"]) == -64228.26
    assert [(x.id, x.reason) for x in d.residue] == [
        ("INV-1003", "no_fx_rate"),
        ("INV-4001", "dismissed"),
    ]
    assert d.period("PLN-2001-A1", date(2025, 10, 1)).status == "missing"
    billing.propose_make_good_invoice("PLN-2001-A1", "2025-10-01", 28000)  # admitted now
    with pytest.raises(DomainError, match="dismissed"):  # cannot link a dismissed invoice
        billing.propose_link_invoice("INV-4001", "PLN-2001-A1")


def test_credit_memo_needs_high_confidence_and_stays_within_the_excess(billing, tmp_path):
    with pytest.raises(DomainError, match="low confidence"):
        low_overbilled_plan(tmp_path).propose_credit_memo("INV-3001", 100)
    with pytest.raises(DomainError, match="excess"):
        billing.propose_credit_memo("INV-1006", 1500.01)
    prop = billing.propose_credit_memo("INV-1006", 1500)
    entry = apply(billing, prop)
    assert entry["action_id"] == "CM-001"
    assert entry["payload"]["invoice_id"] == "INV-1006" and entry["payload"]["currency"] == "USD"
    may = billing.detect_anomalies().period("PLN-1001", date(2026, 5, 1))
    assert (may.status, float(may.net), float(may.credited)) == ("ok", 7000, 1500)
    assert may.memos[-1].source == "sandbox"
    with pytest.raises(DomainError, match="not admissible"):
        billing.propose_credit_memo("INV-1006", 1)
    with pytest.raises(DomainError, match="not admissible"):  # a correct period
        billing.propose_credit_memo("INV-1001", 1)
    with pytest.raises(DomainError, match="no plan"):
        billing.propose_credit_memo("INV-4001", 1)
    billing.rollback("CM-001")
    assert float(billing.detect_anomalies().period("PLN-1001", date(2026, 5, 1)).net) == 8500


def test_amendment_new_version_and_rollback(billing):
    prop = billing.propose_plan_amendment("PLN-1001", "2026-03-01", {"total_value": 105000})
    assert any("8,750 USD per Monthly period" in e for e in prop.evidence)
    entry = apply(billing, prop)
    assert entry["action_id"] == "AMD-001"
    assert entry["payload"]["total_value"] == 105000
    assert entry["payload"]["plan_id"] == "PLN-1001" and entry["payload"]["effective_from"]
    plan = billing.load_plan("PLN-1001")
    assert [v["total_value"] for v in plan["versions"]] == [84000, 105000]
    assert plan["versions"][1]["source"] == "sandbox" and plan["source"] == "sandbox"
    d = billing.detect_anomalies("PLN-1001")
    expected = [float(r.expected) for r in d.plan("PLN-1001").periods]
    assert expected == [7000, 7000, 7000, 8750, 8750, 8750, 8750]
    assert float(d.plan("PLN-1001").totals["expected"]) == 56000
    # an amendment on the amended plan builds on the latest version
    with pytest.raises(DomainError, match="before the current version"):
        billing.propose_plan_amendment("PLN-1001", "2025-12-01", {"total_value": 1})
    billing.rollback("AMD-001")
    assert float(billing.detect_anomalies("PLN-1001").plan("PLN-1001").totals["expected"]) == 49000


def test_amendment_changing_cadence(billing):
    prop = billing.propose_plan_amendment(
        "PLN-1001", "2026-03-01", {"cadence": "Quarterly", "total_value": 84000}
    )
    apply(billing, prop)
    periods = billing.detect_anomalies("PLN-1001").plan("PLN-1001").periods
    assert [(r.period.start.isoformat(), float(r.expected)) for r in periods] == [
        ("2025-12-01", 7000),
        ("2026-01-01", 7000),
        ("2026-02-01", 7000),
        ("2026-03-01", 21000),
        ("2026-06-01", 21000),
    ]


@pytest.mark.parametrize(
    ("plan_id", "eff", "change_set", "match"),
    [
        ("PLN-1001", "2026-03-01", {"currency": "EUR"}, "identity"),
        ("PLN-1001", "2026-03-01", {"start_date": "2025-12-15"}, "identity"),
        ("PLN-1001", "2026-03-01", {"plan_id": "X"}, "identity"),
        ("PLN-1001", "2026-03-01", {"customer_name": "X"}, "identity"),
        ("PLN-1001", "2026-03-01", {"color": "red"}, "cannot amend"),
        ("PLN-1001", "2026-03-01", {}, "non-empty"),
        ("PLN-1001", "2026-03-01", {"total_value": -5}, "greater than 0"),
        ("PLN-1001", "2026-03-01", {"total_value": 84000}, "does not change"),
        ("PLN-1001", "2026-03-01", {"cadence": "Weekly"}, "cadence"),
        ("PLN-1001", "2026-03-01", {"entitlements": []}, "entitlements"),
        ("PLN-1001", "2026-03-15", {"total_value": 1}, "not the start of a period"),
        ("PLN-1001", "2025-11-30", {"total_value": 1}, "outside the term"),
        ("PLN-1001", "2026-12-01", {"total_value": 1}, "outside the term"),
        ("PLN-1001", "2026-04-01", {"cadence": "Quarterly"}, "new cadence"),
        ("PLN-2001", "2025-07-01", {"total_value": 1}, "replaced by PLN-2001-A1"),
        ("PLN-1001", "garbage", {"total_value": 1}, "YYYY-MM-DD"),
    ],
)
def test_invalid_amendments(billing, plan_id, eff, change_set, match):
    with pytest.raises(DomainError, match=match):
        billing.propose_plan_amendment(plan_id, eff, change_set)
    assert billing.list_actions() == []


def test_amendment_of_the_replacing_plan_is_allowed(billing):
    billing.propose_plan_amendment("PLN-2001-A1", "2025-10-01", {"notes": "Q4 renegotiated"})


def test_audit_log_entry_shape(billing):
    entry = apply(billing, feb_make_good(billing))
    assert set(entry) == {
        "action_id", "type", "proposal_id", "thread_id", "plan_id", "payload", "evidence",
        "reason", "applied_at", "rolled_back_at",
    }  # fmt: skip
    assert (entry["type"], entry["proposal_id"], entry["thread_id"]) == (
        "make_good_invoice", "PRP-001", "t1",
    )  # fmt: skip
    assert billing.list_actions() == [entry]


def test_affected_plans(billing):
    link = apply(billing, billing.propose_link_invoice("INV-4001", "PLN-2001-A1"))
    assert billing.affected_plans(link) == ["PLN-2001-A1"]
    billing.rollback("LNK-001")
    mg = apply(billing, feb_make_good(billing), 2)
    assert billing.affected_plans(mg) == ["PLN-1001"]


def test_ids_per_type_are_independent(billing):
    apply(billing, feb_make_good(billing, 3000))
    assert apply(billing, billing.propose_dismiss_invoice("INV-4001"), 2)["action_id"] == "DSM-001"
    assert apply(
        billing, billing.propose_make_good_invoice("PLN-1001", "2026-02-01", 4000), 3
    )["action_id"] == "INV-MG-002"  # fmt: skip


def test_concurrent_applies_get_unique_ids(billing):
    from concurrent.futures import ThreadPoolExecutor

    props = [feb_make_good(billing, 100).to_dict() for _ in range(8)]
    with ThreadPoolExecutor(8) as pool:
        entries = list(
            pool.map(lambda p: billing.apply(p, proposal_id="PRP-001", thread_id="t"), props)
        )
    ids = sorted(e["action_id"] for e in entries)
    assert ids == [f"INV-MG-{i:03d}" for i in range(1, 9)]
    assert len(billing.list_actions()) == 8
    feb = billing.detect_anomalies().period("PLN-1001", date(2026, 2, 1))
    assert float(feb.net) == 800
