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


def test_unattributed_credit_does_not_claim_refund_or_reconciliation():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    credit = snapshot["post_incident_observation"]

    assert Decimal(credit["credited_amount"]) == Decimal("9.7000000")
    assert credit["operation_binding"] is None
    assert credit["classification"] == "unattributed_credit"
    assert "refund" not in credit["classification"]
