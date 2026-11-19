"""Example conversation adapted to PLN-1001, against the real LLM (make eval-llm).

Structural asserts only (tools called, events, state, side effects), never on the text.
"""

import pytest

from app.agent.service import create_service
from app.config import settings
from tests.conftest import DATA_DIR, DATA_THROUGH

pytestmark = pytest.mark.llm


@pytest.fixture
def svc(tmp_path):
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY is empty")
    return create_service(
        data_dir=DATA_DIR, state_dir=tmp_path / "state", clock=lambda: DATA_THROUGH
    )


def calls(turn) -> list[tuple[str, dict]]:
    return [(e.name, e.args) for e in turn.events if e.type == "tool_call"]


def kinds(turn) -> list[str]:
    return [e.type for e in turn.events]


def test_example_conversation(svc):
    tid = svc.create_thread().thread_id

    # 1. Investigate: the detector runs, nothing is proposed on its own initiative.
    t = svc.send_message(
        tid, "Can you check if there are any revenue leakage issues with plan PLN-1001?"
    )
    assert any(n == "detect_anomalies" and a.get("plan_id") == "PLN-1001" for n, a in calls(t))
    findings = next(e for e in t.events if e.type == "findings")
    assert findings.findings.plans[0].plan_id == "PLN-1001"
    assert findings.findings.totals.diff == -5500
    assert [
        p.period_start.month for p in findings.findings.plans[0].periods if p.status == "missing"
    ] == [2]
    assert t.state.active_plan_id == "PLN-1001"
    assert "proposal" not in kinds(t) and "approval_request" not in kinds(t)
    assert "assistant_message" in kinds(t)

    # 2. A follow-up that refers to the previous context: no action either.
    t = svc.send_message(tid, "What currency is that plan in?")
    assert "assistant_message" in kinds(t)
    assert not {"proposal", "approval_request"} & set(kinds(t))
    assert t.state.active_plan_id == "PLN-1001"

    # 3. Propose the make-good for February: validated by the detector, still not applied.
    t = svc.send_message(
        tid, "Can you create a make-good invoice for the missing February billing?"
    )
    proposals = [e.proposal for e in t.events if e.type == "proposal"]
    assert len(proposals) == 1
    assert proposals[0].type == "make_good_invoice" and proposals[0].plan_id == "PLN-1001"
    assert proposals[0].args["period_start"] == "2026-02-01" and proposals[0].args["amount"] == 7000
    assert proposals[0].evidence and proposals[0].reason
    assert t.state.actions == []
    if t.state.pending_approval is None:
        t = svc.send_message(tid, "Yes, apply it.")
        assert any(n == "apply" for n, _ in calls(t))
    card = next(e for e in t.events if e.type == "approval_request")
    assert (
        card.action.kind == "apply" and card.action.proposal.proposal_id == proposals[0].proposal_id
    )
    assert t.state.pending_approval.interrupt_id == card.interrupt_id and t.state.actions == []

    # 5. The user approves in the card: the write happens, the plan is rechecked.
    t = svc.decide(tid, card.interrupt_id, "approve")
    applied = next(e for e in t.events if e.type == "tool_result" and e.name == "apply")
    assert applied.status == "ok" and applied.result["action_id"] == "INV-MG-001"
    assert [a.action_id for a in t.state.actions] == ["INV-MG-001"]
    assert t.state.findings[0].totals.diff == 1500
    assert "assistant_message" in kinds(t)

    # 6. The other months: it may answer from the recheck it already has or read again; it
    #    proposes nothing and applies nothing.
    t = svc.send_message(tid, "What about the other months? Are they correct?")
    assert "assistant_message" in kinds(t)
    assert not {"proposal", "approval_request"} & set(kinds(t))
    assert len(t.state.actions) == 1

    # 7. Rollback goes through the same card.
    t = svc.send_message(tid, "Please roll back that make-good invoice.")
    card = next(e for e in t.events if e.type == "approval_request")
    assert card.action.kind == "rollback" and card.action.action_id == "INV-MG-001"
    t = svc.decide(tid, card.interrupt_id, "approve")
    assert t.state.actions[0].rolled_back_at is not None
    assert t.state.findings[0].totals.diff == -5500

    # 8. Reload shows the same history.
    assert [e.type for e in svc.get_thread(tid).events].count("approval_request") == 2


def test_reject_and_out_of_domain(svc):
    tid = svc.create_thread().thread_id
    svc.send_message(tid, "Check plan PLN-1001 for revenue leakage.")
    t = svc.send_message(tid, "Make a make-good invoice for February 2026 for that plan.")
    assert any(e.type == "proposal" for e in t.events)
    if t.state.pending_approval is None:
        t = svc.send_message(tid, "Apply it.")
    card = next(e for e in t.events if e.type == "approval_request")
    t = svc.decide(tid, card.interrupt_id, "reject")
    assert next(e for e in t.events if e.type == "tool_result").status == "rejected_by_user"
    assert t.state.actions == [] and t.state.proposals[0].status == "rejected"
    t = svc.send_message(tid, "What is the capital of France?")
    assert calls(t) == [] and "assistant_message" in kinds(t)
