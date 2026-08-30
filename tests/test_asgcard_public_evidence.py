import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "evidence" / "asgcard-issue17-stellar-mainnet-snapshot.json"
FIXTURE = ROOT / "fixtures" / "astra" / "stellar_asgcard_settled_without_delivery.json"


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_asgcard_fixture_is_bound_to_the_public_stellar_snapshot():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    settlement = snapshot["authoritative_settlement_evidence"]
    incident = snapshot["incident_claims"]

    assert settlement["successful"] is True
    assert settlement["asset_code"] == "USDC"
    assert Decimal(settlement["debited_amount"]) == Decimal("35.8800000")
    assert Decimal(settlement["credited_amount"]) == Decimal("35.8800000")
    assert _time(settlement["created_at"]) > _time(incident["started_at"])
    assert (
        _time(settlement["created_at"]) - _time(incident["started_at"])
    ).total_seconds() == 39

    finality_events = [
        event
        for event in fixture["events"]
        if event["stage"] == "ACTUAL SETTLEMENT/FINALITY"
    ]
    status = next(event for event in finality_events if event["key"] == "payment_status")
    amount = next(event for event in finality_events if event["key"] == "settled_amount_minor")

    assert status["authoritative"] is True
    assert status["value"] == "settled"
    assert status["payment_id"] == settlement["transaction_hash"]
    assert amount["payment_id"] == settlement["transaction_hash"]
    assert amount["value"] == 3588


def test_operation_binding_is_strongly_correlated_but_not_overstated():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    binding = snapshot["operation_binding"]

    assert binding["confidence"] == "high_contextual"
    assert len(binding["supporting_signals"]) == 3
    assert "exact 402 challenge or signed payment envelope" in binding["missing_evidence"]
    assert any(
        "cryptographically unique" in claim
        for claim in snapshot["claim_boundary"]["not_supported"]
    )


def test_soroban_settlement_is_not_lost_when_payment_view_is_empty():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    ledger_view = snapshot["ledger_view_divergence"]

    assert ledger_view["account_payments_candidate_outgoing_usdc_count"] == 0
    assert ledger_view["account_payments_matching_record_types"] == [
        "invoke_host_function"
    ]
    assert ledger_view["account_effects_matching_debit_count"] == 1
    assert ledger_view["account_effects_matching_credit_count"] == 1


def test_unattributed_credit_does_not_claim_refund_or_reconciliation():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    credit = snapshot["post_incident_observation"]

    assert Decimal(credit["credited_amount"]) == Decimal("9.7000000")
    assert credit["operation_binding"] is None
    assert credit["classification"] == "unattributed_credit"
    assert "refund" not in credit["classification"]
