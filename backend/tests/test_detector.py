"""Detector against the ground truth of docs/design.md (Base and settings/cutoff variants)."""

from datetime import date
from decimal import Decimal

import pytest

from app.domain.errors import DomainError, NotFoundError
from app.domain.knowledge import concept_keys, get_concept
from app.domain.periods import proration_ratio
from tests.helpers import billing_at, diffs, statuses

TODAY_2026 = date(2026, 11, 18)


def test_base_pln1001(billing):
    d = billing.detect_anomalies()
    rows = statuses(d, "PLN-1001")
    assert [r[1] for r in rows] == ["ok", "ok", "missing", "ok", "ok", "overbilled", "ok"]
    assert [r[2] for r in rows if r[1] == "missing"] == [-7000.0]
    p = d.plan("PLN-1001")
    assert p.confidence == "high"
    assert {k: float(v) for k, v in p.totals.items()} == {
        "expected": 49000, "invoiced": 43500, "credited": 0, "net": 43500, "difference": -5500,
    }  # fmt: skip
    apr = d.period("PLN-1001", date(2026, 4, 1))
    assert apr.invoices[0].flags == ["unpaid"]
    assert apr.invoices[0].days_from_period_start == 5


def test_base_pln2001_prorated_stub(billing):
    d = billing.detect_anomalies()
    assert statuses(d, "PLN-2001") == [("2025-05-15", "missing", -10728.26)]
    p = d.plan("PLN-2001")
    assert float(p.totals["expected"]) == 10728.26
    assert p.confidence == "low"
    assert p.low_because == ["period_calendar", "amendment_proration"]
    stub = d.period("PLN-2001", date(2025, 5, 15))
    assert stub.period.end == date(2025, 6, 30) and stub.period.truncated_by == "amendment"


def test_base_pln2001_a1_fx_and_memo(billing):
    d = billing.detect_anomalies()
    q3 = d.period("PLN-2001-A1", date(2025, 7, 1))
    assert q3.status == "ok"
    line = q3.invoices[0]
    assert (float(line.amount), line.currency, float(line.amount_in_plan_currency)) == (
        30000, "EUR", 33000,
    )  # fmt: skip
    assert line.flags == ["currency_mismatch"] and line.days_from_period_start == 50
    assert float(q3.credited) == 5000 and float(q3.net) == 28000
    q4 = d.period("PLN-2001-A1", date(2025, 10, 1))
    assert (q4.status, q4.underlying_status, q4.pending_invoice_ids) == (
        "pending_decision", "missing", ["INV-4001"],
    )  # fmt: skip
    assert d.plan("PLN-2001-A1").confidence == "high"


def test_base_pln3001_and_totals(billing):
    d = billing.detect_anomalies()
    assert statuses(d, "PLN-3001") == [("2025-07-15", "underbilled", -20000.0)]
    assert d.plan("PLN-3001").confidence == "low"
    assert d.plan("PLN-3001").low_because == ["period_calendar"]
    assert diffs(d) == {
        "PLN-1001": -5500,
        "PLN-2001": -10728.26,
        "PLN-2001-A1": -28000,
        "PLN-3001": -20000,
    }
    assert {k: float(v) if k != "currency" else v for k, v in d.totals.items()} == {
        "expected": 291728.26, "invoiced": 232500, "credited": 5000, "net": 227500,
        "difference": -64228.26, "currency": "USD",
    }  # fmt: skip


def test_base_residue_and_freshness(billing):
    d = billing.detect_anomalies()
    assert [(x.id, x.reason, x.candidates) for x in d.residue] == [
        ("INV-1003", "no_fx_rate", []),
        ("INV-4001", "no_plan", ["PLN-2001-A1"]),
    ]
    assert float(d.residue[1].amount) == 15000
    assert d.freshness == {
        "gap": False, "no_data_periods": 0, "amount_at_stake": 0,
        "data_up_to_date_fixed": False, "needs_question": False,
    }  # fmt: skip
    a = d.assumptions
    assert (a["as_of"], a["as_of_inferred"], a["data_through"]) == (
        "2026-06-30", True, "2026-06-30",
    )  # fmt: skip
    assert "billing_in_advance" in a["rules"]


def test_every_record_is_placed_or_in_the_residue(billing):
    d = billing.detect_anomalies()
    placed = {i.invoice_id for p in d.plans for r in p.periods for i in r.invoices}
    placed |= {x.id for x in d.residue if x.kind == "invoice"}
    assert placed == {i["invoice_id"] for i in billing.query_invoices()}
    memos = {m.memo_id for p in d.plans for r in p.periods for m in r.memos}
    assert memos == {"CR-001"}


def test_calendar_variant(billing):
    billing.update_settings({"period_calendar": "calendar"})
    d = billing.detect_anomalies()
    assert statuses(d, "PLN-2001") == [("2025-05-15", "missing", -10846.15)]
    assert d.plan("PLN-2001").confidence == "high"
    s = d.plan("PLN-3001")
    assert [(r.status, float(r.expected)) for r in s.periods] == [
        ("overbilled", 55890.41),
        ("missing", 64109.59),
    ]
    assert float(s.totals["difference"]) == -20000
    assert s.confidence == "high"
    assert float(d.totals["difference"]) == -64346.15


def test_full_period_variant(billing):
    billing.update_settings({"amendment_proration": "full_period"})
    d = billing.detect_anomalies()
    assert statuses(d, "PLN-2001") == [("2025-05-15", "missing", -21000.0)]
    assert float(d.plan("PLN-2001").totals["expected"]) == 21000
    assert d.plan("PLN-2001").low_because == ["period_calendar"]
    assert float(d.totals["difference"]) == -74500


def test_fixed_assumptions_give_high_confidence(billing):
    billing.update_settings({"period_calendar": "anchored", "amendment_proration": "prorate"})
    d = billing.detect_anomalies()
    assert {p.plan.plan_id: p.confidence for p in d.plans} == {
        "PLN-1001": "high", "PLN-2001": "high", "PLN-2001-A1": "high", "PLN-3001": "high",
    }  # fmt: skip
    assert float(d.totals["difference"]) == -64228.26


def test_auto_renewal_changes_nothing_before_the_cutoff(billing):
    billing.update_settings({"auto_renewal": True})
    assert float(billing.detect_anomalies().totals["difference"]) == -64228.26


def test_cutoff_2026_without_freshness_answer(tmp_path):
    b = billing_at(tmp_path, TODAY_2026)
    d = b.detect_anomalies()
    assert float(d.totals["difference"]) == -64228.26
    a1 = d.plan("PLN-2001-A1")
    assert [r.status for r in a1.periods] == ["ok", "pending_decision", "ok", "ok"]
    assert d.freshness == {
        "gap": True, "no_data_periods": 5, "amount_at_stake": 35000,
        "data_up_to_date_fixed": False, "needs_question": True,
    }  # fmt: skip
    assert d.data_through == date(2026, 6, 30)  # never moved by the clock
    assert d.plan("PLN-1001").confidence == "high"


def test_cutoff_2026_data_up_to_date(tmp_path):
    b = billing_at(tmp_path, TODAY_2026)
    b.update_settings({"data_up_to_date": True})
    d = b.detect_anomalies()
    assert diffs(d) == {
        "PLN-1001": -40500,
        "PLN-2001": -10728.26,
        "PLN-2001-A1": -28000,
        "PLN-3001": -20000,
    }
    assert float(d.totals["difference"]) == -99228.26
    assert d.freshness["gap"] is False
    assert d.plan("PLN-2001-A1").low_because == ["auto_renewal"]
    assert d.plan("PLN-3001").low_because == ["period_calendar", "auto_renewal"]


def test_cutoff_2026_with_auto_renewal(tmp_path):
    b = billing_at(tmp_path, TODAY_2026)
    b.update_settings({"data_up_to_date": True, "auto_renewal": True})
    d = b.detect_anomalies()
    assert diffs(d) == {
        "PLN-1001": -40500,
        "PLN-2001": -10728.26,
        "PLN-2001-A1": -84000,
        "PLN-3001": -140000,
    }
    assert float(d.totals["difference"]) == -275228.26


def test_cutoff_before_the_data(tmp_path):
    b = billing_at(tmp_path, TODAY_2026)
    b.update_settings({"as_of": "2026-02-28"})
    d = b.detect_anomalies()
    assert diffs(d) == {
        "PLN-1001": -7000,
        "PLN-2001": -10728.26,
        "PLN-2001-A1": -28000,
        "PLN-3001": -20000,
    }
    assert float(d.totals["difference"]) == -65728.26
    reasons = {x.id: x.reason for x in d.residue}
    assert reasons == {
        "INV-1003": "no_fx_rate",
        "INV-1004": "after_as_of",
        "INV-1005": "after_as_of",
        "INV-1006": "after_as_of",
        "INV-1007": "after_as_of",
        "INV-2003": "after_as_of",
        "INV-4001": "no_plan",
    }
    assert d.period("PLN-2001-A1", date(2025, 10, 1)).status == "pending_decision"
    assert d.data_through == date(2026, 2, 28)


def test_scope_one_plan(billing):
    d = billing.detect_anomalies("PLN-2001-A1")
    assert [p.plan.plan_id for p in d.plans] == ["PLN-2001-A1"]
    assert [x.id for x in d.residue] == ["INV-4001"]
    with pytest.raises(NotFoundError):
        billing.detect_anomalies("PLN-9999")


def test_findings_have_deterministic_ids(billing):
    ids = {f["id"] for f in billing.detect_anomalies().findings()}
    assert "PLN-1001:missing:2026-02-01" in ids
    assert "PLN-2001-A1:missing:2025-10-01" in ids  # pending_decision keeps the underlying type
    assert ids == {f["id"] for f in billing.detect_anomalies().findings()}


def test_proration_ratio_whole_months_and_days():
    assert proration_ratio(
        date(2026, 6, 1), date(2026, 7, 1), date(2026, 4, 1), date(2026, 7, 1), 3
    ) == Decimal(1) / Decimal(3)
    assert proration_ratio(
        date(2025, 5, 15), date(2025, 7, 1), date(2025, 5, 15), date(2025, 8, 15), 3
    ) == Decimal(47) / Decimal(92)


def test_fx_is_strict(billing):
    assert billing.fx_convert(30000, "EUR", "USD", "2025-08-20")["converted"] == 33000
    for args in [(100, "EUR", "USD", "2025-08-21"), (100, "USD", "EUR", "2025-08-20")]:
        with pytest.raises(DomainError):
            billing.fx_convert(*args)
    assert billing.fx_convert(100, "USD", "USD", "2030-01-01")["converted"] == 100


def test_settings_validation(billing, tmp_path):
    s = billing.update_settings({"auto_renewal": True})
    assert s.version == 1
    for bad in [{"auto_renewal": "yes"}, {"period_calendar": "x"}, {"nope": 1},
                {"as_of": "2030-01-01"}, {"as_of": "garbage"}]:  # fmt: skip
        with pytest.raises(DomainError):
            billing.update_settings(bad)
    assert billing.settings().version == 1  # nothing written on error
    s = billing.update_settings({"auto_renewal": None})
    assert (s.version, s.is_fixed("auto_renewal")) == (2, False)
    desc = billing.describe_settings()
    assert desc["data_through"] == "2026-06-30"


def test_query_invoices_and_plans(billing):
    inv = billing.query_invoices(customer_name="Vertex Dynamics")
    assert [i["invoice_id"] for i in inv] == [
        "INV-2001", "INV-2002", "INV-2003", "INV-4001",
    ]  # fmt: skip
    assert inv[0]["credit_memos"][0]["memo_id"] == "CR-001"
    assert inv[3]["plan_id"] is None
    assert [i["invoice_id"] for i in billing.query_invoices(plan_id="PLN-3001")] == ["INV-3001"]
    assert [i["invoice_id"] for i in billing.query_invoices(date_from="2026-06-01")] == ["INV-1007"]
    plans = {p["plan_id"]: p for p in billing.list_plans()}
    assert plans["PLN-2001"]["replaced_by"] == "PLN-2001-A1"
    assert billing.load_plan("PLN-2001-A1")["versions"][0]["total_value"] == 112000
    with pytest.raises(NotFoundError):
        billing.load_plan("nope")


def test_knowledge_base():
    keys = concept_keys()
    for k in ["credit_memo", "plan_amendment", "make_good_invoice", "revenue_leakage",
              "billing_in_advance", "period_calendar", "pending_decision", "no_plan",
              "dismissed", "auto_renewal", "data_up_to_date", "as_of"]:  # fmt: skip
        assert k in keys
    assert "negative invoice" in get_concept("credit_memo")["text"]
    with pytest.raises(DomainError, match="valid keys"):
        get_concept("nope")
