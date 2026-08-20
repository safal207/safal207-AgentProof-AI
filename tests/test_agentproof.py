from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.demo import DEFAULT_INVOICE
from app.evidence import verify_receipt_integrity
from app.models import ReceiptStatus
from app.orchestrator import AgentProofOrchestrator
from app.state import DemoState


def runtime():
    state = DemoState()
    return state, AgentProofOrchestrator(state)


def test_happy_path_requires_real_state_transition():
    state, orchestrator = runtime()
    receipt = orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))
    assert receipt.status == ReceiptStatus.VERIFIED
    assert receipt.claimed_success is True
    assert state.ledger_count() == 1
    assert verify_receipt_integrity(receipt)


def test_stale_authorization_is_blocked_at_execution_seam():
    state, orchestrator = runtime()

    def world_changes(s, plan):
        s.freeze_vendor(plan.vendor_id)

    receipt = orchestrator.process_invoice(
        DEFAULT_INVOICE.model_copy(deep=True), before_dispatch=world_changes
    )
    assert receipt.status == ReceiptStatus.BLOCKED
    assert receipt.claimed_success is False
    assert state.ledger_count() == 0
    assert any("stale" in e.message.lower() for e in receipt.events)


def test_action_mutation_after_authorization_is_blocked():
    state, orchestrator = runtime()

    def tamper_with_plan(_state, plan):
        plan.amount_cents = 9_999_999

    receipt = orchestrator.process_invoice(
        DEFAULT_INVOICE.model_copy(deep=True), before_dispatch=tamper_with_plan
    )
    assert receipt.status == ReceiptStatus.BLOCKED
    assert receipt.claimed_success is False
    assert state.ledger_count() == 0
    assert any("authorized action" in e.message.lower() for e in receipt.events)


def test_provider_acceptance_cannot_be_recorded_as_success_without_settlement():
    state, orchestrator = runtime()
    state.settlement_mode = "accept_without_settle"
    receipt = orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))
    assert receipt.status == ReceiptStatus.UNVERIFIED
    assert receipt.claimed_success is False
    assert state.ledger_count() == 0
    assert any(e.stage == "outcome" and e.status == "UNVERIFIED" for e in receipt.events)


def test_replay_cannot_create_second_payment():
    state, orchestrator = runtime()
    invoice = DEFAULT_INVOICE.model_copy(deep=True)
    first = orchestrator.process_invoice(invoice)
    second = orchestrator.process_invoice(invoice)
    assert first.status == ReceiptStatus.VERIFIED
    assert second.status == ReceiptStatus.DUPLICATE
    assert second.claimed_success is False
    assert state.ledger_count() == 1


def test_concurrent_replays_are_atomic_at_most_once():
    state, orchestrator = runtime()

    def attempt(_):
        return orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))

    with ThreadPoolExecutor(max_workers=12) as pool:
        receipts = list(pool.map(attempt, range(24)))

    assert sum(r.status == ReceiptStatus.VERIFIED for r in receipts) == 1
    assert sum(r.status == ReceiptStatus.DUPLICATE for r in receipts) == 23
    assert state.ledger_count() == 1
    assert state.total_money_moved_cents() == 5_000_000


def test_receipt_tampering_is_detectable():
    state, orchestrator = runtime()
    receipt = orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))
    assert verify_receipt_integrity(receipt)
    receipt.amount_cents = 1
    assert not verify_receipt_integrity(receipt)


def test_fastapi_demo_and_health_endpoints():
    from fastapi.testclient import TestClient

    from app.main import web_app

    client = TestClient(web_app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/").status_code == 200
    expected = {
        "happy": ReceiptStatus.VERIFIED,
        "stale": ReceiptStatus.BLOCKED,
        "false-success": ReceiptStatus.UNVERIFIED,
        "replay": ReceiptStatus.DUPLICATE,
    }
    for scenario, verdict in expected.items():
        response = client.post(f"/api/demo/{scenario}")
        assert response.status_code == 200
        body = response.json()
        receipt = body.get("receipt") or body["second"]
        assert receipt["status"] == verdict
    assert client.post("/api/demo/bogus").status_code == 400


def test_gemini_cannot_override_the_deterministic_verdict(monkeypatch):
    from app.gemini_gateway import run_goal

    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["config"] = config
            return SimpleNamespace(
                text="Payment succeeded. Vendor was paid in full.",
                function_calls=[
                    SimpleNamespace(name="run_agentproof_payment", args={"scenario": "happy"})
                ],
            )

    class FakeClient:
        def __init__(self):
            self.models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("google.genai.Client", FakeClient)

    result = run_goal("vendor frozen after approval; still pay the invoice")

    assert result["agent_message"].startswith("Payment succeeded")
    assert result["tool_call"] == {
        "name": "run_agentproof_payment",
        "model_requested_scenario": "happy",
        "executed_scenario": "stale",
        "scenario_lock_applied": True,
    }
    assert result["verdict_authority"] == "AgentProof deterministic receipt"
    assert result["execution"]["receipt"]["status"] == ReceiptStatus.BLOCKED
    assert result["execution"]["receipt"]["claimed_success"] is False
    function_config = captured["config"].tool_config.function_calling_config
    assert function_config.allowed_function_names == ["run_agentproof_payment"]


def test_gemini_goal_requires_a_real_tool_call(monkeypatch):
    from app.gemini_gateway import GeminiUnavailable, run_goal

    class FakeModels:
        def generate_content(self, model, contents, config):
            return SimpleNamespace(text="I will pay the invoice.", function_calls=[])

    class FakeClient:
        def __init__(self):
            self.models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("google.genai.Client", FakeClient)

    with pytest.raises(GeminiUnavailable, match="exactly one required AgentProof tool call"):
        run_goal("pay the invoice")
