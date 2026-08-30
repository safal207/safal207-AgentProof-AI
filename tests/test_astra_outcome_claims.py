from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


def test_failed_operation_outcome_can_conflict_with_authoritative_settlement():
    findings = verify_causal_economic_outcome(
        [
            StateEvent(
                stage=Stage.CLAIMED_RESULT,
                key="outcome_status",
                value="failed",
                source="merchant-http-502",
                operation_id="op-1",
            ),
            StateEvent(
                stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                key="payment_status",
                value="settled",
                source="independent-ledger",
                authoritative=True,
                operation_id="op-1",
                payment_id="tx-1",
            ),
        ]
    )

    by_code = {finding.code: finding for finding in findings}
    assert by_code["CLAIMED_FAILED_BUT_SETTLED"].severity == "critical"
    assert by_code["CLAIMED_FAILED_BUT_SETTLED"].evidence_sources == (
        "merchant-http-502",
        "independent-ledger",
    )


def test_operation_status_normalization_does_not_mutate_input_event():
    claim = StateEvent(
        stage=Stage.CLAIMED_RESULT,
        key="operation_status",
        value="failed",
        source="merchant",
    )

    verify_causal_economic_outcome([claim])

    assert claim.key == "operation_status"
