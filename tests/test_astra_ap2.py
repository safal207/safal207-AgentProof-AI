import pytest

from app.astra_ap2 import AP2AttemptObservation, normalize_ap2_checkout
from app.astra_spider import Stage, verify_causal_economic_outcome


def codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def test_ap2_failed_psp_cannot_leave_token_and_order_terminal():
    events = normalize_ap2_checkout(
        operation_id="ap2-purchase-001",
        attempts=[
            AP2AttemptObservation(
                attempt_id="attempt-001",
                authorization_id="authorization-001",
                claimed_status="not_settled",
                psp_status="not_settled",
                token_used=True,
                order_id="order-001",
                next_action="token_already_used",
                commitment_timing="before_settlement",
            )
        ],
    )

    assert codes(events) == {
        "NONSETTLED_OPERATION_MARKED_NONRETRYABLE",
        "TERMINAL_COMMITMENT_WITHOUT_SETTLEMENT",
    }


def test_ap2_safe_resume_preserves_operation_and_authorization():
    events = normalize_ap2_checkout(
        operation_id="ap2-purchase-002",
        attempts=[
            AP2AttemptObservation(
                attempt_id="attempt-002-a",
                authorization_id="authorization-002",
                claimed_status="not_settled",
                psp_status="not_settled",
                next_action="retry_settlement",
            ),
            AP2AttemptObservation(
                attempt_id="attempt-002-b",
                authorization_id="authorization-002",
                claimed_status="settled",
                psp_status="settled",
                token_used=True,
                order_id="order-002",
                next_action="complete",
                payment_id="psp-payment-002",
                commitment_timing="after_settlement",
            ),
        ],
        receipt_status="settled",
        delivery_status="delivered",
        reconciliation_status="complete",
    )

    assert verify_causal_economic_outcome(events) == []

    attempts = [
        event
        for event in events
        if event.stage == Stage.PAYMENT_ATTEMPT and event.key == "attempt"
    ]
    assert {event.operation_id for event in attempts} == {"ap2-purchase-002"}
    assert {event.authorization_id for event in attempts} == {"authorization-002"}

    settlements = [
        event
        for event in events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key == "payment_status"
        and event.value == "settled"
        and event.authoritative
    ]
    assert [event.payment_id for event in settlements] == ["psp-payment-002"]


def test_ap2_terminal_commitment_before_success_keeps_interruption_window_visible():
    events = normalize_ap2_checkout(
        operation_id="ap2-purchase-003",
        attempts=[
            AP2AttemptObservation(
                attempt_id="attempt-003",
                authorization_id="authorization-003",
                claimed_status="settled",
                psp_status="settled",
                token_used=True,
                order_id="order-003",
                next_action="complete",
                payment_id="psp-payment-003",
                commitment_timing="before_settlement",
            )
        ],
        receipt_status="settled",
        delivery_status="delivered",
        reconciliation_status="complete",
    )

    assert "TERMINAL_COMMITMENT_PRECEDES_SETTLEMENT" in codes(events)


def test_ap2_adapter_rejects_missing_attempts():
    with pytest.raises(ValueError, match="attempts"):
        normalize_ap2_checkout(operation_id="ap2-purchase-004", attempts=[])
