from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


PAYMENT_ID = "tx-candidate-1"
OPERATION_ID = "operation-1"


def _events(
    binding_status: str = "unresolved",
    *,
    binding_authoritative: bool = False,
) -> list[StateEvent]:
    return [
        StateEvent(
            stage=Stage.CLAIMED_RESULT,
            key="outcome_status",
            value="failed",
            source="merchant-http-502",
            operation_id=OPERATION_ID,
        ),
        StateEvent(
            stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
            key="candidate_payment_status",
            value="settled",
            source="public-ledger",
            authoritative=True,
            payment_id=PAYMENT_ID,
        ),
        StateEvent(
            stage=Stage.RECONCILIATION,
            key="settlement_operation_binding",
            value={
                "status": binding_status,
                "confidence": "high_contextual",
                "payment_id": PAYMENT_ID,
            },
            source="correlation-layer",
            authoritative=binding_authoritative,
            operation_id=OPERATION_ID,
        ),
    ]


def test_unbound_candidate_settlement_does_not_become_operation_finality():
    findings = verify_causal_economic_outcome(_events())
    by_code = {finding.code: finding for finding in findings}

    assert set(by_code) == {"SETTLEMENT_OPERATION_BINDING_UNRESOLVED"}
    finding = by_code["SETTLEMENT_OPERATION_BINDING_UNRESOLVED"]
    assert finding.severity == "high"
    assert finding.operation_id == OPERATION_ID
    assert finding.evidence_sources == (
        "merchant-http-502",
        "public-ledger",
        "correlation-layer",
    )
    assert "CLAIMED_FAILED_BUT_SETTLED" not in by_code
    assert "SETTLED_BUT_NOT_DELIVERED" not in by_code


def test_authoritatively_bound_operation_uses_normal_payment_finality_path():
    findings = verify_causal_economic_outcome(
        [
            StateEvent(
                stage=Stage.CLAIMED_RESULT,
                key="outcome_status",
                value="failed",
                source="merchant-http-502",
                operation_id=OPERATION_ID,
            ),
            StateEvent(
                stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                key="payment_status",
                value="settled",
                source="provider-bound-ledger-evidence",
                authoritative=True,
                operation_id=OPERATION_ID,
                payment_id=PAYMENT_ID,
            ),
        ]
    )
    codes = {finding.code for finding in findings}

    assert "CLAIMED_FAILED_BUT_SETTLED" in codes
    assert "SETTLEMENT_OPERATION_BINDING_UNRESOLVED" not in codes


def test_non_authoritative_bound_marker_does_not_close_gap():
    findings = verify_causal_economic_outcome(_events(binding_status="bound"))

    assert "SETTLEMENT_OPERATION_BINDING_UNRESOLVED" in {
        finding.code for finding in findings
    }


def test_authoritative_bound_marker_closes_gap():
    findings = verify_causal_economic_outcome(
        _events(binding_status="bound", binding_authoritative=True)
    )

    assert "SETTLEMENT_OPERATION_BINDING_UNRESOLVED" not in {
        finding.code for finding in findings
    }
