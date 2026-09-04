from app.astra_settlement_gate import verify_settlement_gated_delivery
from app.astra_spider import Stage, StateEvent


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {finding.code for finding in verify_settlement_gated_delivery(events)}


def contract(operation_id=None):
    return event(
        Stage.POLICY_DECISION,
        "requires_settlement_gated_delivery",
        {
            "required": True,
            "protected_body_must_remain_discardable_until_settled": True,
        },
        "settlement-gate-contract",
        operation_id=operation_id,
    )


def verification(operation_id="op-1"):
    return event(
        Stage.CLAIMED_RESULT,
        "payment_verification_status",
        "verified",
        "facilitator-verify",
        operation_id=operation_id,
        attempt_id="attempt-1",
        authorization_id="auth-1",
        payment_id="payment-1",
    )


def state(value, operation_id="op-1"):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "protected_response_state",
        value,
        "response-wrapper",
        operation_id=operation_id,
        attempt_id="attempt-1",
        authorization_id="auth-1",
        payment_id="payment-1",
    )


def finality(operation_id="op-1"):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        "settled",
        "independent-finality",
        authoritative=True,
        operation_id=operation_id,
        attempt_id="attempt-1",
        authorization_id="auth-1",
        payment_id="payment-1",
    )


def delivery(operation_id="op-1"):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_status",
        "delivered",
        "protected-client",
        operation_id=operation_id,
        attempt_id="attempt-1",
        authorization_id="auth-1",
        payment_id="payment-1",
    )


def test_buffered_output_without_terminal_finality_remains_unresolved():
    assert codes(
        [
            contract("op-1"),
            verification(),
            state("buffered"),
        ]
    ) == {"SETTLEMENT_GATE_FINALITY_EVIDENCE_MISSING"}


def test_global_contract_applies_without_spurious_global_missing_event():
    assert codes(
        [
            contract(None),
            verification(),
            state("buffered"),
            finality(),
            state("flushed"),
            delivery(),
        ]
    ) == set()
