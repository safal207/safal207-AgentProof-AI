from pathlib import Path

from app.astra_settlement_gate import verify_settlement_gated_delivery
from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_settlement_gate"
EXPECTED_FIXTURES = {
    "x402_java_buffer_discarded_on_failure.json",
    "x402_java_buffered_until_settled.json",
    "x402_java_delivery_before_failed_settlement.json",
    "x402_java_delivery_before_late_settlement.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {finding.code for finding in verify_settlement_gated_delivery(events)}


def all_codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def contract(operation_id="op-1", *, provenance=False, valid=True):
    value = {
        "required": True,
        "protected_body_must_remain_discardable_until_settled": valid,
        "implementation_provenance_required": provenance,
    }
    return event(
        Stage.POLICY_DECISION,
        "requires_settlement_gated_delivery",
        value,
        "settlement-gate-contract",
        operation_id=operation_id,
    )


def provenance(operation_id="op-1", *, authoritative=True):
    return event(
        Stage.RECONCILIATION,
        "implementation_artifact",
        {
            "language": "java",
            "artifact": "org.x402:x402 PaymentFilter",
            "commit": "5df361d",
        },
        "build-manifest",
        authoritative=authoritative,
        operation_id=operation_id,
    )


def verification(
    operation_id="op-1",
    *,
    payment_id="payment-1",
    authorization_id="auth-1",
    attempt_id="attempt-1",
    observed_at="2026-09-04T10:00:01Z",
):
    return event(
        Stage.CLAIMED_RESULT,
        "payment_verification_status",
        "verified",
        "facilitator-verify",
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        attempt_id=attempt_id,
        observed_at=observed_at,
    )


def response_state(
    state,
    operation_id="op-1",
    *,
    payment_id="payment-1",
    authorization_id="auth-1",
    attempt_id="attempt-1",
    observed_at=None,
):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "protected_response_state",
        state,
        "response-wrapper",
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        attempt_id=attempt_id,
        observed_at=observed_at,
    )


def finality(
    status,
    operation_id="op-1",
    *,
    payment_id="payment-1",
    authorization_id="auth-1",
    attempt_id="attempt-1",
    observed_at=None,
):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        status,
        "independent-finality",
        authoritative=True,
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        attempt_id=attempt_id,
        observed_at=observed_at,
    )


def delivery(
    operation_id="op-1",
    *,
    payment_id="payment-1",
    authorization_id="auth-1",
    attempt_id="attempt-1",
    observed_at=None,
):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_status",
        "delivered",
        "protected-client",
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        attempt_id=attempt_id,
        observed_at=observed_at,
    )


def test_invalid_contract_fails_closed():
    assert codes([contract(valid=False)]) == {
        "SETTLEMENT_GATED_DELIVERY_CONTRACT_INVALID"
    }


def test_contract_without_successful_verification_is_unresolved():
    assert codes([contract()]) == {"PAYMENT_VERIFICATION_EVIDENCE_MISSING"}


def test_required_implementation_provenance_must_be_authoritative():
    found = codes(
        [
            contract(provenance=True),
            provenance(authoritative=False),
            verification(),
            response_state("buffered"),
            finality("failed"),
            response_state("discarded"),
        ]
    )
    assert found == {"SETTLEMENT_GATE_IMPLEMENTATION_PROVENANCE_MISSING"}


def test_missing_response_gate_evidence_is_unresolved_even_when_settled():
    found = codes(
        [
            contract(),
            verification(),
            finality("settled"),
            delivery(),
        ]
    )
    assert found == {"PROTECTED_RESPONSE_GATE_EVIDENCE_MISSING"}


def test_public_commit_before_late_settlement_is_detected():
    found = codes(
        [
            contract(),
            verification(),
            response_state("generated", observed_at="2026-09-04T10:00:02Z"),
            response_state("committed", observed_at="2026-09-04T10:00:03Z"),
            delivery(observed_at="2026-09-04T10:00:03.1Z"),
            finality("settled", observed_at="2026-09-04T10:00:04Z"),
        ]
    )
    assert found == {
        "PROTECTED_DELIVERY_PRECEDES_SETTLEMENT",
        "PROTECTED_RESPONSE_COMMITTED_BEFORE_SETTLEMENT",
    }


def test_failed_settlement_after_delivery_is_critical():
    findings = verify_settlement_gated_delivery(
        [
            contract(),
            verification(),
            response_state("generated", observed_at="2026-09-04T10:00:02Z"),
            response_state("committed", observed_at="2026-09-04T10:00:03Z"),
            delivery(observed_at="2026-09-04T10:00:03.1Z"),
            finality("failed", observed_at="2026-09-04T10:00:04Z"),
        ]
    )
    finding = next(
        item
        for item in findings
        if item.code == "SETTLEMENT_FAILED_AFTER_PROTECTED_DELIVERY"
    )
    assert finding.severity == "critical"


def test_settlement_before_flush_and_delivery_is_verified():
    found = codes(
        [
            contract(),
            verification(),
            response_state("generated", observed_at="2026-09-04T10:00:02Z"),
            response_state("buffered", observed_at="2026-09-04T10:00:03Z"),
            finality("settled", observed_at="2026-09-04T10:00:04Z"),
            response_state("flushed", observed_at="2026-09-04T10:00:05Z"),
            delivery(observed_at="2026-09-04T10:00:05.1Z"),
        ]
    )
    assert found == set()


def test_failed_settlement_discards_private_body():
    found = codes(
        [
            contract(),
            verification(),
            response_state("buffered", observed_at="2026-09-04T10:00:02Z"),
            finality("failed", observed_at="2026-09-04T10:00:03Z"),
            response_state("discarded", observed_at="2026-09-04T10:00:04Z"),
        ]
    )
    assert found == set()


def test_failed_settlement_with_retained_body_is_high_severity():
    found = codes(
        [
            contract(),
            verification(),
            response_state("buffered", observed_at="2026-09-04T10:00:02Z"),
            finality("failed", observed_at="2026-09-04T10:00:03Z"),
            response_state("retained", observed_at="2026-09-04T10:00:04Z"),
        ]
    )
    assert found == {"PROTECTED_BODY_NOT_DISCARDED_AFTER_SETTLEMENT_FAILURE"}


def test_failed_settlement_without_disposal_evidence_is_unresolved():
    found = codes(
        [
            contract(),
            verification(),
            response_state("buffered", observed_at="2026-09-04T10:00:02Z"),
            finality("failed", observed_at="2026-09-04T10:00:03Z"),
        ]
    )
    assert found == {"PROTECTED_BODY_DISPOSAL_EVIDENCE_MISSING"}


def test_public_delivery_after_already_failed_settlement_is_critical():
    found = codes(
        [
            contract(),
            verification(),
            response_state("buffered", observed_at="2026-09-04T10:00:02Z"),
            finality("failed", observed_at="2026-09-04T10:00:03Z"),
            response_state("flushed", observed_at="2026-09-04T10:00:04Z"),
            delivery(observed_at="2026-09-04T10:00:04.1Z"),
        ]
    )
    assert "PROTECTED_DELIVERY_WITH_FAILED_SETTLEMENT" in found


def test_wrong_payment_identity_is_not_accepted_as_gate_finality():
    found = codes(
        [
            contract(),
            verification(),
            response_state("buffered"),
            finality("settled", payment_id="payment-2"),
            response_state("flushed"),
            delivery(),
        ]
    )
    assert "SETTLEMENT_GATE_IDENTITY_DIVERGENCE" in found
    assert "PROTECTED_DELIVERY_FINALITY_UNRESOLVED" in found


def test_attempt_only_correlation_remains_unresolved():
    weak_finality = event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        "settled",
        "weak-finality-record",
        authoritative=True,
        operation_id="op-1",
        attempt_id="attempt-1",
    )
    found = codes(
        [
            contract(),
            verification(),
            response_state("buffered"),
            weak_finality,
            response_state("flushed"),
            delivery(),
        ]
    )
    assert "SETTLEMENT_GATE_IDENTITY_UNRESOLVED" in found


def test_findings_are_isolated_by_operation():
    bad = [
        contract("op-bad"),
        verification("op-bad", payment_id="payment-bad"),
        response_state("committed", "op-bad", payment_id="payment-bad"),
        delivery("op-bad", payment_id="payment-bad"),
        finality("failed", "op-bad", payment_id="payment-bad"),
    ]
    good = [
        contract("op-good"),
        verification("op-good", payment_id="payment-good"),
        response_state("buffered", "op-good", payment_id="payment-good"),
        finality("settled", "op-good", payment_id="payment-good"),
        response_state("flushed", "op-good", payment_id="payment-good"),
        delivery("op-good", payment_id="payment-good"),
    ]
    findings = verify_settlement_gated_delivery([*bad, *good])
    assert findings
    assert {finding.operation_id for finding in findings} == {"op-bad"}


def test_all_settlement_gate_fixtures_match_oracles():
    paths = sorted(FIXTURES.glob("*.json"))
    assert {path.name for path in paths} == EXPECTED_FIXTURES

    for path in paths:
        trace = load_trace(path)
        report = build_trace_report(trace)
        assert {finding.code for finding in report.findings} == set(
            trace.expected_codes
        )
        assert report.verdict == trace.expected_verdict
        assert len(report.evidence_hash) == 64
