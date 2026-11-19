"""The real agent graph with ScriptedLLM and MemorySaver: no network (agent.md, Testing)."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.agent.service import RealAgentService
from app.domain.billing import Billing
from app.main import app
from app.schemas import SettingsUpdate
from app.service import Conflict, ThreadNotFound, get_service
from tests.conftest import DATA_DIR
from tests.fake_llm import ScriptedLLM, ai, call


@pytest.fixture
def llm():
    return ScriptedLLM()


@pytest.fixture
def svc(billing, llm):
    return RealAgentService(billing, llm, MemorySaver())


def types(turn) -> list[str]:
    return [e.type for e in turn.events]


def say(svc, tid, text="go"):
    return svc.send_message(tid, text)


def approval(turn):
    return next(e for e in turn.events if e.type == "approval_request")


def new_thread(svc) -> str:
    t = svc.create_thread()
    assert t.events == [] and t.state.pending_approval is None
    return t.thread_id


def propose_feb(llm, svc, tid, n=1):
    llm.queue(
        ai(
            call(
                "propose_make_good_invoice",
                f"p{n}",
                plan_id="PLN-1001",
                period_start="2026-02-01",
                amount=7000,
            )
        ),
        ai(text="Proposed."),
    )
    return say(svc, tid, "make-good for february")


def test_investigation_turn(svc, llm):
    tid = new_thread(svc)
    llm.queue(
        ai(call("detect_anomalies", "c1", plan_id="PLN-1001")), ai(text="24,000 USD is missing.")
    )
    turn = say(svc, tid, "check PLN-1001")
    assert types(turn) == [
        "user_message", "tool_call", "tool_result", "findings", "assistant_message",
    ]  # fmt: skip
    findings = turn.events[3].findings
    assert (findings.totals.expected, findings.totals.net, findings.totals.diff) == (
        49000,
        43500,
        -5500,
    )
    assert turn.events[2].status == "ok" and turn.events[2].result["totals"]["diff"] == -5500
    s = turn.state
    assert (s.active_plan_id, s.active_customer) == ("PLN-1001", "Alder Creek Logistics")
    assert s.findings[0].plan_id == "PLN-1001" and s.findings[0].stale is False
    # the model got a static prompt and a second system message with the verified state
    first_call = llm.seen[0]
    assert [m.type for m in first_call[:3]] == ["system", "system", "human"]
    second_call = llm.seen[1]
    assert "Active plan: PLN-1001" in second_call[1].content
    assert second_call[-1].type == "tool"


def test_propose_then_approve(svc, llm, billing):
    tid = new_thread(svc)
    turn = propose_feb(llm, svc, tid)
    prop = next(e for e in turn.events if e.type == "proposal")
    assert prop.proposal.proposal_id == "PRP-001" and prop.proposal.status == "pending"
    assert prop.proposal.args["period_start"] == "2026-02-01"
    assert billing.list_actions() == []

    llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
    turn = say(svc, tid, "yes, apply it")
    assert types(turn) == ["user_message", "tool_call", "approval_request"]
    card = approval(turn)
    assert card.action.kind == "apply" and card.action.proposal.proposal_id == "PRP-001"
    assert card.tool_call_id == "a1" and card.action.evidence
    assert turn.state.pending_approval.interrupt_id == card.interrupt_id
    assert billing.list_actions() == []  # a "yes" in the chat writes nothing

    llm.queue(ai(text="Applied INV-MG-001."))
    turn = svc.decide(tid, card.interrupt_id, "approve")
    assert types(turn) == [
        "tool_result", "tool_call", "tool_result", "findings", "assistant_message",
    ]  # fmt: skip
    assert turn.events[0].tool_call_id == "a1" and turn.events[0].status == "ok"
    assert turn.events[0].result["action_id"] == "INV-MG-001"
    assert turn.events[1].name == "detect_anomalies"  # D23: the affected plan is rechecked
    assert turn.events[3].findings.totals.diff == 1500
    s = turn.state
    assert s.pending_approval is None and [a.action_id for a in s.actions] == ["INV-MG-001"]
    assert s.actions[0].thread_id == tid and s.actions[0].proposal_id == "PRP-001"
    assert s.proposals[0].status == "applied" and s.findings[0].totals.diff == 1500
    # the model saw the real ID before confirming
    tool_msgs = [m.content for m in llm.seen[-1] if m.type == "tool"]
    assert any("INV-MG-001" in c for c in tool_msgs)


def test_reject_writes_nothing(svc, llm, billing):
    tid = new_thread(svc)
    propose_feb(llm, svc, tid)
    llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
    card = approval(say(svc, tid, "apply"))
    llm.queue(ai(text="Nothing was written."))
    turn = svc.decide(tid, card.interrupt_id, "reject")
    assert types(turn) == ["tool_result", "assistant_message"]
    assert turn.events[0].status == "rejected_by_user"
    assert turn.state.proposals[0].status == "rejected" and turn.state.actions == []
    assert not (billing.state_dir / "sandbox" / "invoices.json").exists()
    # a rejected proposal cannot be applied later
    llm.queue(ai(call("apply", "a2", proposal_id="PRP-001")), ai(text="It was rejected."))
    turn = say(svc, tid, "apply it anyway")
    assert "approval_request" not in types(turn)
    assert turn.events[2].status == "error" and "rejected" in turn.events[2].result["error"]


def test_guarded_action_mixed_with_reads_in_one_message(svc, llm):
    tid = new_thread(svc)
    llm.queue(
        ai(
            call(
                "propose_make_good_invoice",
                "p1",
                plan_id="PLN-1001",
                period_start="2026-02-01",
                amount=7000,
            )
        ),
        ai(text="ok"),
    )
    say(svc, tid, "propose")
    llm.queue(
        ai(
            call("detect_anomalies", "r1", plan_id="PLN-1001"),
            call("apply", "a1", proposal_id="PRP-001"),
            call("query_invoices", "r2", plan_id="PLN-1001"),
        )
    )
    turn = say(svc, tid, "check and apply")
    # reads ran, the guarded call is waiting; every tool call gets exactly one result at the end
    assert types(turn).count("approval_request") == 1
    names = [e.name for e in turn.events if e.type == "tool_result"]
    assert sorted(names) == ["detect_anomalies", "query_invoices"]
    llm.queue(ai(text="done"))
    turn = svc.decide(tid, approval(turn).interrupt_id, "approve")
    assert turn.events[0].tool_call_id == "a1" and turn.state.actions[0].action_id == "INV-MG-001"


def test_several_guarded_actions_in_a_row(svc, llm):
    tid = new_thread(svc)
    llm.queue(
        ai(
            call(
                "propose_make_good_invoice",
                "p1",
                plan_id="PLN-1001",
                period_start="2026-02-01",
                amount=3000,
            ),
            call(
                "propose_make_good_invoice",
                "p2",
                plan_id="PLN-1001",
                period_start="2026-02-01",
                amount=4000,
            ),
        ),
        ai(text="two proposals"),
    )
    turn = say(svc, tid, "propose february twice")
    assert [e.proposal.proposal_id for e in turn.events if e.type == "proposal"] == [
        "PRP-001",
        "PRP-002",
    ]
    llm.queue(
        ai(call("apply", "a1", proposal_id="PRP-001"), call("apply", "a2", proposal_id="PRP-002"))
    )
    turn = say(svc, tid, "apply both")
    first = approval(turn)
    assert first.tool_call_id == "a1"
    turn = svc.decide(tid, first.interrupt_id, "approve")
    # the second card is pending now; the recheck waits for the last guarded call
    second = approval(turn)
    assert second.tool_call_id == "a2" and second.interrupt_id != first.interrupt_id
    assert "findings" not in types(turn)
    assert turn.state.pending_approval.interrupt_id == second.interrupt_id
    llm.queue(ai(text="both applied"))
    turn = svc.decide(tid, second.interrupt_id, "approve")
    assert types(turn) == [
        "tool_result",
        "tool_call",
        "tool_result",
        "findings",
        "assistant_message",
    ]
    assert turn.events[3].findings.totals.diff == 1500
    assert [a.action_id for a in turn.state.actions] == ["INV-MG-001", "INV-MG-002"]


def test_invalid_actions_get_an_error_and_no_card(svc, llm):
    tid = new_thread(svc)
    llm.queue(
        ai(
            call(
                "propose_make_good_invoice",
                "p1",
                plan_id="PLN-3001",
                period_start="2025-07-15",
                amount=100,
            )
        ),
        ai(text="cannot"),
    )
    turn = say(svc, tid, "propose for lumen health")
    result = next(e for e in turn.events if e.type == "tool_result")
    assert result.status == "error" and "low confidence" in result.result["error"]
    assert "proposal" not in types(turn) and turn.state.proposals == []
    llm.queue(ai(call("apply", "a1", proposal_id="PRP-009")), ai(text="no such proposal"))
    turn = say(svc, tid, "apply PRP-009")
    assert "approval_request" not in types(turn) and turn.state.pending_approval is None
    llm.queue(ai(call("rollback", "r1", action_id="INV-MG-001")), ai(text="nothing to roll back"))
    turn = say(svc, tid, "roll back INV-MG-001")
    assert "approval_request" not in types(turn)
    assert turn.events[2].status == "error"
    llm.queue(
        ai(call("nope", "n1")), ai(call("detect_anomalies", "d1", plan_id="X-1")), ai(text="x")
    )
    turn = say(svc, tid, "weird")
    # an unknown tool, then an unknown plan: both come back as errors, the turn goes on
    assert [e.status for e in turn.events if e.type == "tool_result"] == ["error", "error"]


def test_applied_twice_and_stale_proposal_is_invalid(svc, llm, billing):
    tid = new_thread(svc)
    propose_feb(llm, svc, tid)
    llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
    card = approval(say(svc, tid, "apply"))
    # the data changes between the proposal and the approval
    billing.apply(
        billing.propose_make_good_invoice("PLN-1001", "2026-02-01", 7000).to_dict(),
        proposal_id="PRP-X",
        thread_id="other",
    )
    llm.queue(ai(text="done"))
    turn = svc.decide(tid, card.interrupt_id, "approve")
    # the revalidation after the resume finds the period already fixed: error, nothing written
    assert turn.events[0].status == "error" and "no longer holds" in turn.events[0].result["error"]
    assert turn.state.proposals[0].status == "invalid"
    assert [a.action_id for a in turn.state.actions] == ["INV-MG-001"]  # only the other thread's
    llm.queue(ai(call("apply", "a2", proposal_id="PRP-001")), ai(text="invalid"))
    turn = say(svc, tid, "try again")
    assert "approval_request" not in types(turn)
    assert turn.state.proposals[0].status == "invalid"


def test_rollback_in_order(svc, llm):
    tid = new_thread(svc)
    propose_feb(llm, svc, tid)
    llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
    card = approval(say(svc, tid, "apply"))
    llm.queue(ai(text="applied"))
    svc.decide(tid, card.interrupt_id, "approve")

    llm.queue(ai(call("rollback", "r1", action_id="INV-MG-001")))
    turn = say(svc, tid, "roll it back")
    card = approval(turn)
    assert card.action.kind == "rollback" and card.action.action_id == "INV-MG-001"
    assert card.action.proposal is None
    llm.queue(ai(text="rolled back"))
    turn = svc.decide(tid, card.interrupt_id, "approve")
    assert turn.events[0].result["status"] == "rolled_back"
    assert turn.events[3].findings.totals.diff == -5500  # back to the base
    assert turn.state.actions[0].rolled_back_at is not None
    assert turn.state.actions[0].payload["ledger_state"] == "rolled_back"


def test_conflicts(svc, llm):
    tid = new_thread(svc)
    propose_feb(llm, svc, tid)
    with pytest.raises(Conflict):  # no pending action
        svc.decide(tid, "int-1", "approve")
    llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
    card = approval(say(svc, tid, "apply"))
    with pytest.raises(Conflict):  # a message while an action is pending
        say(svc, tid, "hello")
    with pytest.raises(Conflict):  # an old or invented card
        svc.decide(tid, "int-other", "approve")
    with pytest.raises(Conflict):  # settings are global and a thread has an action pending
        svc.update_settings(SettingsUpdate(auto_renewal=True))
    llm.queue(ai(text="ok"))
    svc.decide(tid, card.interrupt_id, "reject")
    with pytest.raises(Conflict):  # the same card twice
        svc.decide(tid, card.interrupt_id, "approve")
    assert svc.update_settings(SettingsUpdate(auto_renewal=True)).version == 1
    with pytest.raises(ThreadNotFound):
        svc.get_thread("thr-nope")
    with pytest.raises(ThreadNotFound):
        say(svc, "thr-nope")


def test_findings_go_stale_after_a_settings_change(svc, llm):
    tid = new_thread(svc)
    llm.queue(ai(call("detect_anomalies", "c1")), ai(text="all plans"))
    turn = say(svc, tid, "check everything")
    assert {f.plan_id for f in turn.state.findings} == {
        "PLN-1001",
        "PLN-2001",
        "PLN-2001-A1",
        "PLN-3001",
    }
    assert not any(f.stale for f in turn.state.findings)
    svc.update_settings(SettingsUpdate(period_calendar="calendar"))
    state = svc.get_thread(tid).state
    assert all(f.stale for f in state.findings) and state.settings_version == 1
    llm.queue(ai(text="ok"))
    say(svc, tid, "hello")
    assert (
        "STALE" in llm.seen[-1][1].content and "period_calendar=calendar" in llm.seen[-1][1].content
    )
    # D23: with stale findings, an applied action recalculates nothing
    llm.queue(
        ai(
            call(
                "propose_make_good_invoice",
                "p1",
                plan_id="PLN-1001",
                period_start="2026-02-01",
                amount=7000,
            )
        ),
        ai(text="p"),
    )
    say(svc, tid, "propose")
    llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
    card = approval(say(svc, tid, "apply"))
    llm.queue(ai(text="applied"))
    turn = svc.decide(tid, card.interrupt_id, "approve")
    assert types(turn) == ["tool_result", "assistant_message"]
    assert all(f.stale for f in turn.state.findings)
    # detecting again clears it
    llm.queue(ai(call("detect_anomalies", "c2")), ai(text="fresh"))
    turn = say(svc, tid, "re-check")
    assert not any(f.stale for f in turn.state.findings)


def test_freshness_is_asked_only_once(tmp_path, llm):
    from tests.helpers import billing_at

    b = billing_at(tmp_path, date(2026, 11, 18))
    svc = RealAgentService(b, llm, MemorySaver())
    tid = new_thread(svc)
    llm.queue(
        ai(call("detect_anomalies", "c1", plan_id="PLN-1001")),
        ai(text="asked"),
        ai(call("detect_anomalies", "c2", plan_id="PLN-1001")),
        ai(text="not again"),
    )
    say(svc, tid, "check PLN-1001")
    say(svc, tid, "check PLN-1001 again")
    results = [
        json.loads(m.content)
        for m in svc.compiled.get_state(svc._config(tid)).values["messages"]
        if m.type == "tool"
    ]
    asks = [r["freshness"]["ask_user_about_freshness"] for r in results]
    assert asks == [True, False]
    assert results[0]["freshness"]["amount_at_stake"] == 35000
    # the first reply is told to ask it; the second turn is told it was asked
    assert "put it to the user in this reply" in llm.seen[1][1].content
    assert "do not ask again" in llm.seen[-1][1].content


def test_llm_failure_is_an_error_event(svc, llm):
    tid = new_thread(svc)
    turn = say(svc, tid, "hello")  # ScriptedLLM has no steps: it raises
    assert types(turn) == ["user_message", "error"]
    assert "ran out of steps" in turn.events[1].message
    llm.queue(ai(text="hi again"))
    turn = say(svc, tid, "still there?")
    assert types(turn) == ["user_message", "assistant_message"]
    assert types(svc.get_thread(tid)) == [
        "user_message",
        "error",
        "user_message",
        "assistant_message",
    ]


def test_reload_shows_what_the_turns_showed(svc, llm):
    tid = new_thread(svc)
    seen = []
    llm.queue(ai(call("detect_anomalies", "c1", plan_id="PLN-1001")), ai(text="found"))
    seen += say(svc, tid, "check").events
    seen += propose_feb(llm, svc, tid).events
    llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
    turn = say(svc, tid, "apply")
    seen += turn.events
    llm.queue(ai(text="done"))
    seen += svc.decide(tid, approval(turn).interrupt_id, "approve").events
    history = svc.get_thread(tid).events
    assert [e.model_dump() for e in history] == [e.model_dump() for e in seen]


def test_graph_is_mermaid(svc):
    text = svc.graph()
    for node in ("agent", "tools", "human_review"):
        assert node in text


def test_plans_and_settings(svc):
    plans = {p.plan_id: p for p in svc.list_plans()}
    assert plans["PLN-2001-A1"].amends == "PLN-2001" and plans["PLN-1001"].source == "data"
    s = svc.get_settings()
    assert (s.version, str(s.data_through)) == (0, "2026-06-30")
    keys = {i.key: i for i in s.settings}
    assert keys["period_calendar"].value is None and keys["period_calendar"].default == "anchored"
    assert keys["data_up_to_date"].default is None and keys["as_of"].valid_values is None
    assert svc.update_settings(SettingsUpdate(period_calendar="calendar")).version == 1
    assert svc.update_settings(SettingsUpdate(period_calendar="calendar")).version == 1  # no change
    assert svc.update_settings(SettingsUpdate(period_calendar=None)).version == 2  # back to default
    from app.service import InvalidSettings

    with pytest.raises(InvalidSettings):
        svc.update_settings(SettingsUpdate(as_of=date(2030, 1, 1)))


def test_routes_over_the_real_service(svc, llm):
    app.dependency_overrides[get_service] = lambda: svc
    try:
        client = TestClient(app)
        tid = client.post("/api/threads").json()["thread_id"]
        assert client.get("/api/threads/nope").status_code == 404
        llm.queue(ai(call("detect_anomalies", "c1", plan_id="PLN-1001")), ai(text="found"))
        r = client.post(f"/api/threads/{tid}/messages", json={"text": "check"})
        assert r.status_code == 200 and r.json()["events"][3]["type"] == "findings"
        llm.queue(
            ai(
                call(
                    "propose_make_good_invoice",
                    "p1",
                    plan_id="PLN-1001",
                    period_start="2026-02-01",
                    amount=7000,
                )
            ),
            ai(text="p"),
        )
        client.post(f"/api/threads/{tid}/messages", json={"text": "propose"})
        llm.queue(ai(call("apply", "a1", proposal_id="PRP-001")))
        body = client.post(f"/api/threads/{tid}/messages", json={"text": "apply"}).json()
        iid = body["state"]["pending_approval"]["interrupt_id"]
        assert client.post(f"/api/threads/{tid}/messages", json={"text": "x"}).status_code == 409
        bad = client.post(
            f"/api/threads/{tid}/decisions", json={"interrupt_id": "z", "decision": "approve"}
        )
        assert bad.status_code == 409
        assert client.put("/api/settings", json={"auto_renewal": True}).status_code == 409
        llm.queue(ai(text="applied"))
        ok = client.post(
            f"/api/threads/{tid}/decisions", json={"interrupt_id": iid, "decision": "approve"}
        )
        assert (
            ok.status_code == 200 and ok.json()["state"]["actions"][0]["action_id"] == "INV-MG-001"
        )
        assert client.put("/api/settings", json={"as_of": "2030-01-01"}).status_code == 422
        assert "agent" in client.get("/api/graph").text
        assert client.get("/api/plans").json()[0]["plan_id"] == "PLN-1001"
    finally:
        app.dependency_overrides.clear()


def test_sqlite_checkpointer_survives_a_restart(tmp_path, llm):
    """D3: a pending approval is still there after the process restarts."""
    from app.agent.service import create_service

    state = tmp_path / "state"
    clock = lambda: date(2026, 6, 30)  # noqa: E731
    one = create_service(llm, data_dir=DATA_DIR, state_dir=state, clock=clock)
    tid = new_thread(one)
    llm.queue(
        ai(
            call(
                "propose_make_good_invoice",
                "p1",
                plan_id="PLN-1001",
                period_start="2026-02-01",
                amount=7000,
            )
        ),
        ai(text="p"),
        ai(call("apply", "a1", proposal_id="PRP-001")),
    )
    say(one, tid, "propose")
    card = approval(say(one, tid, "apply"))
    two = create_service(llm, data_dir=DATA_DIR, state_dir=state, clock=clock)
    assert two.get_thread(tid).state.pending_approval.interrupt_id == card.interrupt_id
    llm.queue(ai(text="applied"))
    turn = two.decide(tid, card.interrupt_id, "approve")
    assert turn.state.actions[0].action_id == "INV-MG-001"
    assert isinstance(Billing(DATA_DIR, state), Billing)


def test_settings_update_with_sqlite_and_threads_does_not_hang(tmp_path, llm):
    """P-QM.2: SqliteSaver.list holds the connection lock; get_state inside the loop deadlocked."""
    import concurrent.futures

    from app.agent.service import create_service

    svc = create_service(
        llm, data_dir=DATA_DIR, state_dir=tmp_path / "state", clock=lambda: date(2026, 6, 30)
    )
    new_thread(svc)
    new_thread(svc)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        fut = pool.submit(svc.update_settings, SettingsUpdate(as_of=date(2026, 5, 31)))
        assert fut.result(timeout=10).version == 1


def test_first_requests_together_share_one_service_and_never_lock(tmp_path, monkeypatch, llm):
    """Several first requests used to open several connections to a new database."""
    import threading

    import app.agent.service as module
    from app.config import settings

    monkeypatch.setattr(settings, "state_dir", tmp_path / "state")
    monkeypatch.setattr(settings, "data_dir", DATA_DIR)
    monkeypatch.setattr(module, "_shared", None)
    found, errors = [], []
    barrier = threading.Barrier(6)

    def first_request():
        try:
            barrier.wait()
            s = module.create_service()
            s.update_settings(SettingsUpdate(auto_renewal=True))
            found.append(s)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=first_request) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len({id(s) for s in found}) == 1


def test_services_built_together_on_one_database_do_not_lock(tmp_path, llm):
    import threading

    from app.agent.service import create_service

    errors = []
    barrier = threading.Barrier(6)

    def build():
        try:
            barrier.wait()
            s = create_service(llm, data_dir=DATA_DIR, state_dir=tmp_path / "state")
            s.update_settings(SettingsUpdate(auto_renewal=True))
            s.create_thread()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=build) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
