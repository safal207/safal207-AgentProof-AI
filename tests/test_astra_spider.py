from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


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
