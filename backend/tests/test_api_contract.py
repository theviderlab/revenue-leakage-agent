"""Contract tests against the mock (D14, D29): full flow, 404, 409, 422."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.service import RealAgentService
from app.config import settings
from app.main import app
from app.mock import MockService
from app.service import build_service, get_service


@pytest.fixture
def client() -> Iterator[TestClient]:
    mock = MockService()
    app.dependency_overrides[get_service] = lambda: mock
    yield TestClient(app)
    app.dependency_overrides.clear()


def new_thread(client: TestClient) -> str:
    response = client.post("/api/threads")
    assert response.status_code == 200
    body = response.json()
    assert body["events"] == []
    assert body["state"]["pending_approval"] is None
    return body["thread_id"]


def say(client: TestClient, thread_id: str, text: str) -> dict:
    response = client.post(f"/api/threads/{thread_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def decide(client: TestClient, thread_id: str, interrupt_id: str, decision: str):
    return client.post(
        f"/api/threads/{thread_id}/decisions",
        json={"interrupt_id": interrupt_id, "decision": decision},
    )


def types(body: dict) -> list[str]:
    return [e["type"] for e in body["events"]]


def propose(client: TestClient, thread_id: str, text: str = "make-good invoice for February"):
    body = say(client, thread_id, text)
    assert "proposal" in types(body)
    return body


def request_approval(client: TestClient, thread_id: str, text: str) -> dict:
    body = say(client, thread_id, text)
    approval = next(e for e in body["events"] if e["type"] == "approval_request")
    assert body["state"]["pending_approval"]["interrupt_id"] == approval["interrupt_id"]
    return approval


def test_selector_picks_the_implementation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "state_dir", tmp_path)
    assert isinstance(build_service("mock"), MockService)
    assert isinstance(build_service("real"), RealAgentService)


def test_full_flow(client: TestClient) -> None:
    thread_id = new_thread(client)

    # Investigate: ground truth of PLN-1001 (Base).
    body = say(client, thread_id, "Check plan PLN-1001 for revenue leakage")
    assert types(body) == [
        "user_message",
        "tool_call",
        "tool_result",
        "findings",
        "assistant_message",
    ]
    findings = next(e for e in body["events"] if e["type"] == "findings")["findings"]
    plan = findings["plans"][0]
    assert plan["confidence"] == "high"
    assert plan["totals"] == {"expected": 49000, "net": 43500, "diff": -5500}
    status = {p["period_start"][5:7]: p["status"] for p in plan["periods"]}
    assert [m for m, s in status.items() if s == "missing"] == ["02"]
    assert body["state"]["active_plan_id"] == "PLN-1001"
    assert body["state"]["actions"] == []

    # Follow-up keeps the context.
    assert "USD" in say(client, thread_id, "What currency is that plan in?")["events"][-1]["text"]

    # Propose: the tool_result and the proposal event share the tool_call_id.
    body = propose(client, thread_id)
    proposal_event = next(e for e in body["events"] if e["type"] == "proposal")
    call = next(e for e in body["events"] if e["type"] == "tool_call")
    assert proposal_event["tool_call_id"] == call["tool_call_id"]
    proposal = proposal_event["proposal"]
    assert proposal["proposal_id"] == "PRP-001"
    assert proposal["args"] == {"plan_id": "PLN-1001", "period_start": "2026-02-01", "amount": 7000}
    assert proposal["status"] == "pending"

    # Apply asks for approval; the thread is blocked meanwhile.
    approval = request_approval(client, thread_id, "Yes, apply it")
    assert approval["action"]["kind"] == "apply"
    assert approval["action"]["proposal"]["proposal_id"] == "PRP-001"
    assert say_status(client, thread_id) == 409

    # Approve: the tool_result of the original call arrives with the decision.
    response = decide(client, thread_id, approval["interrupt_id"], "approve")
    assert response.status_code == 200
    body = response.json()
    result = next(e for e in body["events"] if e["type"] == "tool_result" and e["name"] == "apply")
    assert result["tool_call_id"] == approval["tool_call_id"]
    assert result["status"] == "ok"
    assert result["result"]["action_id"] == "INV-MG-001"
    assert body["state"]["pending_approval"] is None
    assert body["state"]["proposals"][0]["status"] == "applied"
    assert [a["action_id"] for a in body["state"]["actions"]] == ["INV-MG-001"]
    assert body["state"]["actions"][0]["rolled_back_at"] is None
    recheck = next(e for e in body["events"] if e["type"] == "findings")["findings"]["plans"][0]
    assert recheck["totals"]["diff"] == 1500
    assert {p["period_start"]: p["status"] for p in recheck["periods"]}["2026-02-01"] == "ok"
    assert "INV-MG-001" in body["events"][-1]["text"]

    # The audit log is global: another thread sees the action.
    other = new_thread(client)
    assert [
        a["action_id"] for a in client.get(f"/api/threads/{other}").json()["state"]["actions"]
    ] == ["INV-MG-001"]

    # Rollback goes through the same pending approval and is reverted on approval.
    approval = request_approval(client, thread_id, "Roll it back")
    assert approval["action"]["kind"] == "rollback"
    assert approval["action"]["action_id"] == "INV-MG-001"
    assert approval["action"]["proposal"] is None
    body = decide(client, thread_id, approval["interrupt_id"], "approve").json()
    assert body["state"]["actions"][0]["rolled_back_at"] is not None
    recheck = next(e for e in body["events"] if e["type"] == "findings")["findings"]["plans"][0]
    assert recheck["totals"]["diff"] == -5500

    # Reload shows the whole history, ending where the turns ended.
    reloaded = client.get(f"/api/threads/{thread_id}").json()
    assert reloaded["events"][0] == {
        "type": "user_message",
        "text": "Check plan PLN-1001 for revenue leakage",
    }
    assert reloaded["events"][-1] == body["events"][-1]
    assert reloaded["state"] == body["state"]


def say_status(client: TestClient, thread_id: str) -> int:
    return client.post(f"/api/threads/{thread_id}/messages", json={"text": "hello"}).status_code


def test_reject_writes_nothing(client: TestClient) -> None:
    thread_id = new_thread(client)
    propose(client, thread_id)
    approval = request_approval(client, thread_id, "apply")
    body = decide(client, thread_id, approval["interrupt_id"], "reject").json()
    result = next(e for e in body["events"] if e["type"] == "tool_result")
    assert result["status"] == "rejected_by_user"
    assert result["tool_call_id"] == approval["tool_call_id"]
    assert body["state"]["actions"] == []
    assert body["state"]["proposals"][0]["status"] == "rejected"
    assert body["state"]["pending_approval"] is None
    # A rejected proposal cannot be applied again.
    assert "no pending proposal" in say(client, thread_id, "apply")["events"][-1]["text"]


def test_reject_rollback_keeps_the_action(client: TestClient) -> None:
    thread_id = new_thread(client)
    propose(client, thread_id)
    approval = request_approval(client, thread_id, "apply")
    decide(client, thread_id, approval["interrupt_id"], "approve")
    approval = request_approval(client, thread_id, "rollback")
    body = decide(client, thread_id, approval["interrupt_id"], "reject").json()
    assert body["state"]["actions"][0]["rolled_back_at"] is None


def test_rollback_out_of_order_is_rejected(client: TestClient) -> None:
    thread_id = new_thread(client)
    propose(client, thread_id, "make-good invoice for February")
    approval = request_approval(client, thread_id, "apply")
    decide(client, thread_id, approval["interrupt_id"], "approve")
    body = say(client, thread_id, "roll back INV-MG-099")
    result = next(e for e in body["events"] if e["type"] == "tool_result")
    assert result["status"] == "error"
    assert "INV-MG-001" in result["result"]["error"]
    assert body["state"]["pending_approval"] is None


def test_proposal_validation_errors_come_back_as_tool_results(client: TestClient) -> None:
    thread_id = new_thread(client)
    for text in ("make-good invoice for October", "make-good invoice for PLN-3001"):
        body = say(client, thread_id, text)
        result = next(e for e in body["events"] if e["type"] == "tool_result")
        assert result["status"] == "error"
        assert body["state"]["proposals"] == []


def test_agent_failure_is_an_error_event_with_200(client: TestClient) -> None:
    thread_id = new_thread(client)
    body = say(client, thread_id, "simulate an error")
    assert types(body) == ["user_message", "error"]


# --- 404 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/api/threads/nope", None),
        ("post", "/api/threads/nope/messages", {"text": "hi"}),
        ("post", "/api/threads/nope/decisions", {"interrupt_id": "x", "decision": "approve"}),
    ],
)
def test_unknown_thread_is_404(client: TestClient, method: str, path: str, json: dict | None):
    assert client.request(method, path, json=json).status_code == 404


# --- 409 ---------------------------------------------------------------------------------------


def test_decision_without_pending_action_is_409(client: TestClient) -> None:
    thread_id = new_thread(client)
    assert decide(client, thread_id, "int-1", "approve").status_code == 409


def test_decision_with_wrong_interrupt_id_is_409_and_keeps_the_card(client: TestClient) -> None:
    thread_id = new_thread(client)
    propose(client, thread_id)
    approval = request_approval(client, thread_id, "apply")
    assert decide(client, thread_id, "stale", "approve").status_code == 409
    state = client.get(f"/api/threads/{thread_id}").json()["state"]
    assert state["pending_approval"]["interrupt_id"] == approval["interrupt_id"]
    assert state["actions"] == []


def test_decision_cannot_be_replayed(client: TestClient) -> None:
    thread_id = new_thread(client)
    propose(client, thread_id)
    approval = request_approval(client, thread_id, "apply")
    assert decide(client, thread_id, approval["interrupt_id"], "approve").status_code == 200
    assert decide(client, thread_id, approval["interrupt_id"], "approve").status_code == 409
    state = client.get(f"/api/threads/{thread_id}").json()["state"]
    assert len(state["actions"]) == 1


def test_message_with_pending_action_is_409(client: TestClient) -> None:
    thread_id = new_thread(client)
    propose(client, thread_id)
    request_approval(client, thread_id, "apply")
    assert say_status(client, thread_id) == 409


def test_settings_change_with_pending_action_in_any_thread_is_409(client: TestClient) -> None:
    thread_id = new_thread(client)
    propose(client, thread_id)
    approval = request_approval(client, thread_id, "apply")
    assert client.put("/api/settings", json={"period_calendar": "calendar"}).status_code == 409
    assert client.get("/api/settings").json()["version"] == 1
    decide(client, thread_id, approval["interrupt_id"], "reject")
    assert client.put("/api/settings", json={"period_calendar": "calendar"}).status_code == 200


# --- plans, settings, 422 ----------------------------------------------------------------------


def test_plans_and_graph(client: TestClient) -> None:
    plans = client.get("/api/plans").json()
    assert [p["plan_id"] for p in plans] == ["PLN-1001", "PLN-2001", "PLN-2001-A1", "PLN-3001"]
    assert {p["source"] for p in plans} == {"data"}
    assert "human_review" in client.get("/api/graph").text


def test_mock_totals_match_the_detector(client: TestClient, billing) -> None:
    """M32: the scripted mock shows the same Base numbers as the deterministic detector."""
    tid = new_thread(client)
    body = say(client, tid, "Check plan PLN-1001")
    mock_plan = next(e for e in body["events"] if e["type"] == "findings")["findings"]["plans"][0]
    real = billing.detect_anomalies("PLN-1001").plan("PLN-1001")
    assert mock_plan["totals"] == {
        "expected": float(real.totals["expected"]),
        "net": float(real.totals["net"]),
        "diff": float(real.totals["difference"]),
    }
    mock_statuses = {
        (p["period_start"], p["status"])
        for p in mock_plan["periods"]
        if p["period_start"] < "2026-07-01"
    }
    real_statuses = {(r.period.start.isoformat(), r.status) for r in real.periods}
    assert mock_statuses == real_statuses


def test_settings_roundtrip_bumps_version_and_makes_findings_stale(client: TestClient) -> None:
    thread_id = new_thread(client)
    say(client, thread_id, "Check plan PLN-1001")
    settings = client.get("/api/settings").json()
    assert settings["version"] == 1
    assert settings["data_through"] == "2026-06-30"
    assert {s["key"]: s["value"] for s in settings["settings"]}["period_calendar"] is None

    updated = client.put("/api/settings", json={"period_calendar": "calendar"}).json()
    assert updated["version"] == 2
    assert {s["key"]: s["value"] for s in updated["settings"]}["period_calendar"] == "calendar"
    state = client.get(f"/api/threads/{thread_id}").json()["state"]
    assert state["settings_version"] == 2
    assert state["findings"][0]["stale"] is True

    # The same value again is not a change (D36): the version stays.
    assert client.put("/api/settings", json={"period_calendar": "calendar"}).json()["version"] == 2

    # null goes back to the default.
    updated = client.put("/api/settings", json={"period_calendar": None}).json()
    assert {s["key"]: s["value"] for s in updated["settings"]}["period_calendar"] is None
    assert updated["version"] == 3


@pytest.mark.parametrize(
    "body", [{"as_of": "2999-01-01"}, {"period_calendar": "weekly"}, {"auto_renewal": "maybe"}]
)
def test_invalid_settings_are_422(client: TestClient, body: dict) -> None:
    assert client.put("/api/settings", json=body).status_code == 422
    assert client.get("/api/settings").json()["version"] == 1


def test_empty_message_is_422(client: TestClient) -> None:
    thread_id = new_thread(client)
    assert client.post(f"/api/threads/{thread_id}/messages", json={"text": ""}).status_code == 422
