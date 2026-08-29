from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def contract(operation_id: str, *, same_authorization_idempotent: bool = True):
    return event(
        Stage.QUOTE_CHALLENGE,
        "requires_resolution_before_fresh_authorization_after_indeterminate",
        {
            "required": True,
            "same_authorization_idempotent": same_authorization_idempotent,
        },
        "retry-contract",
        operation_id=operation_id,
    )


def attempt(operation_id: str, attempt_id: str, payment_id: str):
    return event(
        Stage.PAYMENT_ATTEMPT,
        "attempt",
        "submitted",
        "client",
        operation_id=operation_id,
        attempt_id=attempt_id,
        payment_id=payment_id,
        authorization_id=payment_id,
    )


def payment_status(
    operation_id: str,
    attempt_id: str,
    payment_id: str,
    value: str,
    *,
    stage: Stage,
    authoritative: bool = False,
):
    return event(
        stage,
        "payment_status",
        value,
        "settlement-source",
        authoritative=authoritative,
        operation_id=operation_id,
        attempt_id=attempt_id,
        payment_id=payment_id,
        authorization_id=payment_id,
    )


def test_fresh_authorization_after_indeterminate_settlement_is_unsafe_not_confirmed_duplicate():
    op = "op-1"
    findings = verify_causal_economic_outcome(
        [
            contract(op),
            attempt(op, "a1", "nonce-1"),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "indeterminate",
                stage=Stage.CLAIMED_RESULT,
            ),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "indeterminate",
                stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                authoritative=True,
            ),
            attempt(op, "a2", "nonce-2"),
        ]
    )

    finding = next(
        item
        for item in findings
        if item.code == "FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT"
    )
    assert finding.severity == "high"
    assert "not proof" in finding.explanation.lower()
    assert "RETRY_DUPLICATE_PAYMENT" not in {item.code for item in findings}


def test_authoritative_not_settled_resolution_allows_fresh_authorization():
    op = "op-2"
    found = codes(
        [
            contract(op),
            attempt(op, "a1", "nonce-1"),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "indeterminate",
                stage=Stage.CLAIMED_RESULT,
            ),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "indeterminate",
                stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                authoritative=True,
            ),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "not_settled",
                stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                authoritative=True,
            ),
            attempt(op, "a2", "nonce-2"),
        ]
    )
    assert "FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT" not in found


def test_same_authorization_resume_is_safe_only_when_contract_declares_idempotency():
    op = "op-3"
    common = [
        attempt(op, "a1", "nonce-1"),
        payment_status(
            op,
            "a1",
            "nonce-1",
            "indeterminate",
            stage=Stage.CLAIMED_RESULT,
        ),
        payment_status(
            op,
            "a1",
            "nonce-1",
            "indeterminate",
            stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
            authoritative=True,
        ),
        attempt(op, "a2", "nonce-1"),
    ]

    assert verify_causal_economic_outcome([contract(op), *common]) == []
    assert "INDETERMINATE_RETRY_IDEMPOTENCY_UNPROVEN" in codes(
        [contract(op, same_authorization_idempotent=False), *common]
    )


def test_fresh_authorization_after_confirmed_settlement_is_critical():
    op = "op-4"
    findings = verify_causal_economic_outcome(
        [
            contract(op),
            attempt(op, "a1", "nonce-1"),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "indeterminate",
                stage=Stage.CLAIMED_RESULT,
            ),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "settled",
                stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                authoritative=True,
            ),
            attempt(op, "a2", "nonce-2"),
        ]
    )
    finding = next(
        item
        for item in findings
        if item.code == "FRESH_AUTHORIZATION_AFTER_CONFIRMED_SETTLEMENT"
    )
    assert finding.severity == "critical"


def test_same_authorization_after_confirmed_settlement_still_requires_idempotency():
    op = "op-5"
    common = [
        attempt(op, "a1", "nonce-1"),
        payment_status(
            op,
            "a1",
            "nonce-1",
            "settled",
            stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
            authoritative=True,
        ),
        attempt(op, "a2", "nonce-1"),
    ]

    unsafe = codes(
        [contract(op, same_authorization_idempotent=False), *common]
    )
    safe = codes([contract(op), *common])
    assert "INDETERMINATE_RETRY_IDEMPOTENCY_UNPROVEN" in unsafe
    assert "INDETERMINATE_RETRY_IDEMPOTENCY_UNPROVEN" not in safe


def test_unresolved_state_survives_same_identity_retry_until_fresh_authorization():
    op = "op-6"
    found = codes(
        [
            contract(op),
            attempt(op, "a1", "nonce-1"),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "indeterminate",
                stage=Stage.CLAIMED_RESULT,
            ),
            attempt(op, "a2", "nonce-1"),
            attempt(op, "a3", "nonce-2"),
        ]
    )
    assert "FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT" in found


def test_claimed_failure_without_authoritative_finality_does_not_clear_retry_risk():
    op = "op-7"
    found = codes(
        [
            contract(op),
            attempt(op, "a1", "nonce-1"),
            payment_status(
                op,
                "a1",
                "nonce-1",
                "failed",
                stage=Stage.CLAIMED_RESULT,
            ),
            attempt(op, "a2", "nonce-2"),
        ]
    )
    assert "FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT" in found


def test_retry_identity_must_be_comparable():
    op = "op-8"
    found = codes(
        [
            contract(op),
            event(
                Stage.PAYMENT_ATTEMPT,
                "attempt",
                "submitted",
                "client",
                operation_id=op,
                attempt_id="a1",
            ),
            event(
                Stage.CLAIMED_RESULT,
                "payment_status",
                "indeterminate",
                "transport",
                operation_id=op,
                attempt_id="a1",
            ),
            event(
                Stage.PAYMENT_ATTEMPT,
                "attempt",
                "submitted",
                "client",
                operation_id=op,
                attempt_id="a2",
            ),
        ]
    )
    assert "RETRY_PAYMENT_IDENTITY_UNRESOLVED" in found
