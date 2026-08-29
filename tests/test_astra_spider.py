from pathlib import Path

from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra"
EXPECTED_FIXTURES = {
    "x402_auth_capture_delivery_before_capture.json",
    "x402_authorization_window.json",
    "x402_batch_settlement_untrusted_state.json",
    "x402_funded_but_merchant_unsettled.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def test_claimed_failed_but_chain_settled_is_critical():
    findings = verify_causal_economic_outcome(
        [
            event(Stage.PAYMENT_ATTEMPT, "attempt", "sent", "client", attempt_id="a1"),
            event(Stage.CLAIMED_RESULT, "payment_status", "failed", "facilitator"),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                "chain",
                authoritative=True,
                payment_id="tx-1",
            ),
        ]
    )
    finding = next(item for item in findings if item.code == "CLAIMED_FAILED_BUT_SETTLED")
    assert finding.severity == "critical"


def test_retry_that_settles_twice_is_detected():
    found = codes(
        [
            event(Stage.PAYMENT_ATTEMPT, "attempt", "sent", "client", attempt_id="a1"),
            event(Stage.PAYMENT_ATTEMPT, "attempt", "sent", "client", attempt_id="a2"),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                "chain",
                authoritative=True,
                payment_id="tx-1",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                "chain",
                authoritative=True,
                payment_id="tx-2",
            ),
        ]
    )
    assert "RETRY_DUPLICATE_PAYMENT" in found


def test_repeated_idempotency_key_with_same_context_is_not_called_replay():
    found = codes(
        [
            event(
                Stage.PAYMENT_ATTEMPT,
                "attempt",
                "sent",
                "client",
                attempt_id="idem-1",
                operation_id="op-1",
                session_id="session-1",
                payload_hash="hash-1",
            ),
            event(
                Stage.PAYMENT_ATTEMPT,
                "attempt",
                "retry",
                "client",
                attempt_id="idem-1",
                operation_id="op-1",
                session_id="session-1",
                payload_hash="hash-1",
            ),
        ]
    )
    assert "ATTEMPT_ID_COLLISION" not in found


def test_same_attempt_id_across_payloads_is_detected():
    found = codes(
        [
            event(
                Stage.PAYMENT_ATTEMPT,
                "attempt",
                "sent",
                "client",
                attempt_id="idem-1",
                payload_hash="hash-1",
            ),
            event(
                Stage.PAYMENT_ATTEMPT,
                "attempt",
                "retry",
                "client",
                attempt_id="idem-1",
                payload_hash="hash-2",
            ),
        ]
    )
    assert "ATTEMPT_ID_COLLISION" in found


def test_settled_but_not_delivered_is_detected():
    found = codes(
        [
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                "psp",
                authoritative=True,
                payment_id="pay-1",
            )
        ]
    )
    assert "SETTLED_BUT_NOT_DELIVERED" in found


def test_delivered_but_not_settled_is_detected():
    found = codes(
        [event(Stage.RESOURCE_OUTCOME_DELIVERY, "delivery_status", "delivered", "merchant")]
    )
    assert "DELIVERED_BUT_NOT_SETTLED" in found


def test_receipt_must_not_override_authoritative_finality():
    found = codes(
        [
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                "chain",
                authoritative=True,
                payment_id="tx-1",
            ),
            event(Stage.RECEIPT, "payment_status", "failed", "merchant-receipt"),
            event(Stage.RESOURCE_OUTCOME_DELIVERY, "delivery_status", "delivered", "merchant"),
            event(Stage.RECONCILIATION, "status", "complete", "ledger"),
        ]
    )
    assert "RECEIPT_FINALITY_MISMATCH" in found
    assert "SETTLED_BUT_NOT_DELIVERED" not in found
    assert "RECONCILIATION_GAP" not in found


def test_happy_path_has_no_findings():
    found = codes(
        [
            event(Stage.PAYMENT_ATTEMPT, "attempt", "sent", "client", attempt_id="a1"),
            event(Stage.CLAIMED_RESULT, "payment_status", "settled", "facilitator"),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                "chain",
                authoritative=True,
                payment_id="tx-1",
            ),
            event(Stage.RECEIPT, "payment_status", "settled", "merchant-receipt"),
            event(Stage.RESOURCE_OUTCOME_DELIVERY, "delivery_status", "delivered", "merchant"),
            event(Stage.RECONCILIATION, "status", "complete", "ledger"),
        ]
    )
    assert found == set()


def test_all_killer_fixtures_match_their_expected_findings():
    paths = sorted(FIXTURES.glob("*.json"))
    assert {path.name for path in paths} == EXPECTED_FIXTURES
    for path in paths:
        trace = load_trace(path)
        report = build_trace_report(trace)
        assert {finding.code for finding in report.findings} == set(trace.expected_codes)
        assert report.verdict == "DIVERGED"
        assert len(report.evidence_hash) == 64


def test_over_capture_is_detected_from_authoritative_amount_evidence():
    found = codes(
        [
            event(
                Stage.MANDATE_AUTHORIZATION,
                "authorized_amount_minor",
                100,
                "mandate",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "captured_amount_minor",
                120,
                "psp",
                authoritative=True,
            ),
        ]
    )
    assert "OVER_CAPTURE" in found


def test_claimed_settled_without_independent_finality_is_detected():
    found = codes(
        [event(Stage.CLAIMED_RESULT, "payment_status", "settled", "facilitator")]
    )
    assert "CLAIMED_SETTLED_WITHOUT_FINALITY" in found
