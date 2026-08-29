from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


def event(stage, key, value, source="test", **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def findings(events):
    return verify_causal_economic_outcome(events)


def codes(events):
    return {finding.code for finding in findings(events)}


def test_funding_without_merchant_settlement_cannot_be_claimed_complete():
    result = findings(
        [
            event(
                Stage.QUOTE_CHALLENGE,
                "required_settlement_legs",
                ["funding", "merchant"],
                operation_id="purchase-1",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "settlement_leg_status",
                {"leg": "funding", "status": "settled"},
                authoritative=True,
                operation_id="purchase-1",
                payment_id="funding-tx-1",
            ),
            event(
                Stage.CLAIMED_RESULT,
                "payment_phase",
                "payment_confirmed",
                source="status-api",
                operation_id="purchase-1",
            ),
            event(
                Stage.CLAIMED_RESULT,
                "next_action",
                "none",
                source="status-api",
                operation_id="purchase-1",
            ),
        ]
    )

    by_code = {finding.code: finding for finding in result}
    assert set(by_code) == {
        "FUNDED_BUT_MERCHANT_UNSETTLED",
        "PARTIAL_SETTLEMENT_CLAIMED_COMPLETE",
        "RECOVERY_ACTION_MISSING",
    }
    assert by_code["FUNDED_BUT_MERCHANT_UNSETTLED"].severity == "critical"
    assert by_code["FUNDED_BUT_MERCHANT_UNSETTLED"].operation_id == "purchase-1"
    assert by_code["RECOVERY_ACTION_MISSING"].evidence_sources == (
        "status-api",
        "test",
    )


def test_all_required_legs_complete_has_no_multileg_finding():
    found = codes(
        [
            event(
                Stage.QUOTE_CHALLENGE,
                "required_settlement_legs",
                ["funding", "merchant"],
                operation_id="purchase-2",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "settlement_leg_status",
                {"leg": "funding", "status": "settled"},
                authoritative=True,
                operation_id="purchase-2",
                payment_id="funding-tx-2",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "settlement_leg_status",
                {"leg": "merchant", "status": "settled"},
                authoritative=True,
                operation_id="purchase-2",
                payment_id="merchant-tx-2",
            ),
        ]
    )
    assert found == set()


def test_non_authoritative_merchant_claim_does_not_complete_the_leg():
    found = codes(
        [
            event(
                Stage.QUOTE_CHALLENGE,
                "required_settlement_legs",
                ["funding", "merchant"],
                operation_id="purchase-3",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "funding_leg_status",
                "settled",
                authoritative=True,
                operation_id="purchase-3",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "merchant_leg_status",
                "settled",
                source="merchant-callback",
                authoritative=False,
                operation_id="purchase-3",
            ),
        ]
    )
    assert "FUNDED_BUT_MERCHANT_UNSETTLED" in found


def test_generic_partial_multileg_flow_remains_unresolved_not_duplicate():
    found = codes(
        [
            event(
                Stage.MANDATE_AUTHORIZATION,
                "required_payment_legs",
                ["reserve", "capture"],
                operation_id="authorization-4",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "reserve_leg_status",
                "confirmed",
                authoritative=True,
                operation_id="authorization-4",
            ),
            event(
                Stage.CLAIMED_RESULT,
                "payment_phase",
                "processing",
                operation_id="authorization-4",
            ),
            event(
                Stage.CLAIMED_RESULT,
                "next_action",
                "retry_capture",
                operation_id="authorization-4",
            ),
        ]
    )
    assert found == {"PARTIAL_SETTLEMENT_OUTCOME_UNRESOLVED"}
