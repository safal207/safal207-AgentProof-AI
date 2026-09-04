from app.astra_settlement_gate import verify_settlement_gated_delivery
from app.astra_spider import Stage, StateEvent


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {finding.code for finding in verify_settlement_gated_delivery(events)}


def contract(operation_id="op-1", *, valid=True):
    return event(
        Stage.POLICY_DECISION,
        "requires_settlement_gated_delivery",
        {
            "required": True,
            "protected_body_must_remain_discardable_until_settled": valid,
        },
        "settlement-gate-contract",
        operation_id=operation_id,
    )


def verification(
    *,
    operation_id="op-1",
    attempt_id="attempt-a",
    authorization_id="auth-a",
    payment_id="payment-a",
    observed_at=None,
):
    return event(
        Stage.CLAIMED_RESULT,
        "payment_verification_status",
        "verified",
        "facilitator-verify",
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
        observed_at=observed_at,
    )


def response_state(
    state,
    *,
    operation_id="op-1",
    attempt_id="attempt-a",
    authorization_id="auth-a",
    payment_id="payment-a",
    observed_at=None,
):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "protected_response_state",
        state,
        "response-wrapper",
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
        observed_at=observed_at,
    )


def finality(
    status,
    *,
    operation_id="op-1",
    attempt_id="attempt-a",
    authorization_id="auth-a",
    payment_id="payment-a",
    observed_at=None,
):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        status,
        "independent-finality",
        authoritative=True,
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
        observed_at=observed_at,
    )


def delivery(
    *,
    operation_id="op-1",
    attempt_id="attempt-a",
    authorization_id="auth-a",
    payment_id="payment-a",
    observed_at=None,
):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_status",
        "delivered",
        "protected-client",
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
        observed_at=observed_at,
    )


def test_fresh_payment_in_same_operation_is_not_identity_divergence():
    events = [
        contract(),
        verification(),
        response_state("buffered"),
        finality("failed"),
        response_state("discarded"),
        verification(
            attempt_id="attempt-b",
            authorization_id="auth-b",
            payment_id="payment-b",
        ),
        response_state(
            "buffered",
            attempt_id="attempt-b",
            authorization_id="auth-b",
            payment_id="payment-b",
        ),
        finality(
            "settled",
            attempt_id="attempt-b",
            authorization_id="auth-b",
            payment_id="payment-b",
        ),
        response_state(
            "flushed",
            attempt_id="attempt-b",
            authorization_id="auth-b",
            payment_id="payment-b",
        ),
        delivery(
            attempt_id="attempt-b",
            authorization_id="auth-b",
            payment_id="payment-b",
        ),
    ]

    assert codes(events) == set()


def test_same_attempt_id_with_different_payment_identity_is_divergent():
    events = [
        contract(),
        verification(),
        response_state("buffered"),
        finality(
            "settled",
            authorization_id="auth-b",
            payment_id="payment-b",
        ),
    ]

    assert codes(events) == {"SETTLEMENT_GATE_IDENTITY_DIVERGENCE"}


def test_partial_typed_identity_match_plus_conflict_is_divergent():
    events = [
        contract(),
        verification(),
        response_state("buffered"),
        finality(
            "settled",
            authorization_id="auth-a",
            payment_id="payment-b",
            attempt_id="attempt-other",
        ),
    ]

    assert codes(events) == {"SETTLEMENT_GATE_IDENTITY_DIVERGENCE"}


def test_public_leak_before_failure_does_not_add_redundant_disposal_finding():
    events = [
        contract(),
        verification(observed_at="2026-09-04T10:00:01Z"),
        response_state("generated", observed_at="2026-09-04T10:00:02Z"),
        response_state("committed", observed_at="2026-09-04T10:00:03Z"),
        delivery(observed_at="2026-09-04T10:00:03.1Z"),
        finality("failed", observed_at="2026-09-04T10:00:04Z"),
    ]

    assert codes(events) == {
        "PROTECTED_DELIVERY_PRECEDES_SETTLEMENT",
        "PROTECTED_RESPONSE_COMMITTED_BEFORE_SETTLEMENT",
        "SETTLEMENT_FAILED_AFTER_PROTECTED_DELIVERY",
    }


def test_observed_time_beats_misleading_trace_order():
    events = [
        contract(),
        verification(observed_at="2026-09-04T10:00:01Z"),
        response_state("buffered", observed_at="2026-09-04T10:00:02Z"),
        # The finality record appears first in the trace but happened later.
        finality("settled", observed_at="2026-09-04T10:00:05Z"),
        response_state("committed", observed_at="2026-09-04T10:00:03Z"),
        delivery(observed_at="2026-09-04T10:00:03.1Z"),
    ]

    assert codes(events) == {
        "PROTECTED_DELIVERY_PRECEDES_SETTLEMENT",
        "PROTECTED_RESPONSE_COMMITTED_BEFORE_SETTLEMENT",
    }


def test_invalid_global_contract_blocks_clean_operation_contract():
    events = [
        contract(operation_id=None, valid=False),
        contract(operation_id="op-1"),
        verification(),
        response_state("buffered"),
        finality("settled"),
        response_state("flushed"),
        delivery(),
    ]

    assert codes(events) == {"SETTLEMENT_GATED_DELIVERY_CONTRACT_INVALID"}


def test_unprotected_error_response_is_not_treated_as_protected_delivery():
    events = [
        contract(),
        verification(),
        response_state("buffered"),
        finality("failed"),
        response_state("discarded"),
        event(
            Stage.CLAIMED_RESULT,
            "error_response_status",
            "http_402_delivered",
            "payment-filter",
            operation_id="op-1",
            attempt_id="attempt-a",
        ),
    ]

    assert codes(events) == set()
