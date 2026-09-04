from pathlib import Path

from app.astra_receipt_finality import verify_independent_receipt_finality_binding
from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_receipt_finality"
EXPECTED_FIXTURES = {
    "ap2_confirmation_id_mismatch.json",
    "ap2_independent_rail_confirmation_reconciled.json",
    "ap2_late_finality_after_receipt.json",
    "ap2_signed_success_generated_ids_no_rail.json",
    "ap2_success_conflicts_failed_finality.json",
    "ap2_verified_flag_without_rail_evidence.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {
        finding.code
        for finding in verify_independent_receipt_finality_binding(events)
    }


def all_codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def contract(operation_id="op-1", value=None):
    return event(
        Stage.POLICY_DECISION,
        "requires_independent_receipt_finality_binding",
        value
        or {
            "required": True,
            "success_requires_settled_finality": True,
            "verified_flag_is_claim_only": True,
        },
        "receipt-finality-contract",
        operation_id=operation_id,
    )


def receipt_status(
    status="Success",
    *,
    operation_id="op-1",
    payment_id="payment-1",
    authorization_id="authorization-1",
    observed_at="2026-08-17T10:00:00Z",
):
    return event(
        Stage.RECEIPT,
        "receipt_status",
        status,
        "payment-receipt",
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        observed_at=observed_at,
    )


def integrity(
    status="verified",
    *,
    authoritative=True,
    operation_id="op-1",
    payment_id="payment-1",
    authorization_id="authorization-1",
    observed_at="2026-08-17T10:00:00Z",
):
    return event(
        Stage.RECEIPT,
        "receipt_integrity_status",
        status,
        "independent-receipt-verifier",
        authoritative=authoritative,
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        observed_at=observed_at,
    )


def receipt_confirmation(
    *,
    psp_id="psp-1",
    network_id="network-1",
    verified_flag=False,
    operation_id="op-1",
    payment_id="payment-1",
    authorization_id="authorization-1",
    observed_at="2026-08-17T10:00:00Z",
):
    return event(
        Stage.RECEIPT,
        "receipt_rail_confirmation",
        {
            "psp_confirmation_id": psp_id,
            "network_confirmation_id": network_id,
            "rail_confirmation_verified": verified_flag,
        },
        "payment-receipt",
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        observed_at=observed_at,
    )


def finality(
    status="settled",
    *,
    operation_id="op-1",
    payment_id="payment-1",
    authorization_id="authorization-1",
    observed_at="2026-08-17T09:59:50Z",
):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        status,
        "independent-rail-resolver",
        authoritative=True,
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        observed_at=observed_at,
    )


def rail_confirmation(
    *,
    psp_id="psp-1",
    network_id="network-1",
    operation_id="op-1",
    payment_id="payment-1",
    authorization_id="authorization-1",
    observed_at="2026-08-17T09:59:51Z",
):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "rail_confirmation",
        {
            "psp_confirmation_id": psp_id,
            "network_confirmation_id": network_id,
        },
        "independent-rail-resolver",
        authoritative=True,
        operation_id=operation_id,
        payment_id=payment_id,
        authorization_id=authorization_id,
        observed_at=observed_at,
    )


def complete_delivery(operation_id="op-1", payment_id="payment-1"):
    return [
        event(
            Stage.RESOURCE_OUTCOME_DELIVERY,
            "delivery_status",
            "delivered",
            "merchant",
            operation_id=operation_id,
            payment_id=payment_id,
        ),
        event(
            Stage.RECONCILIATION,
            "status",
            "complete",
            "operation-ledger",
            authoritative=True,
            operation_id=operation_id,
            payment_id=payment_id,
        ),
    ]


def valid_receipt(*, verified_flag=True):
    return [
        integrity(),
        receipt_status(),
        receipt_confirmation(verified_flag=verified_flag),
    ]


def verified_lifecycle(*, verified_flag=True):
    return [
        contract(),
        finality(),
        rail_confirmation(),
        *valid_receipt(verified_flag=verified_flag),
        *complete_delivery(),
    ]


def test_invalid_contract_fails_closed():
    invalid = contract(value={"required": True})
    assert codes([invalid]) == {"RECEIPT_FINALITY_BINDING_CONTRACT_INVALID"}


def test_required_contract_without_receipt_status_is_unresolved():
    assert codes([contract()]) == {"RECEIPT_STATUS_EVIDENCE_MISSING"}


def test_success_receipt_without_integrity_or_rail_evidence_is_unresolved():
    found = codes(
        [
            contract(),
            receipt_status(),
            receipt_confirmation(),
        ]
    )
    assert found == {
        "RECEIPT_INTEGRITY_EVIDENCE_MISSING",
        "RECEIPT_RAIL_CONFIRMATION_EVIDENCE_MISSING",
        "RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY",
    }


def test_failed_receipt_integrity_is_not_promoted_by_real_settlement():
    found = codes(
        [
            contract(),
            finality(),
            rail_confirmation(),
            integrity("failed"),
            receipt_status(),
            receipt_confirmation(verified_flag=True),
            *complete_delivery(),
        ]
    )
    assert found == {
        "RECEIPT_INTEGRITY_FAILED",
        "RECEIPT_RAIL_VERIFICATION_CLAIM_UNSUPPORTED",
    }


def test_conflicting_integrity_results_fail_closed():
    found = codes(
        [
            contract(),
            finality(),
            rail_confirmation(),
            integrity("verified"),
            integrity("failed"),
            receipt_status(),
            receipt_confirmation(),
            *complete_delivery(),
        ]
    )
    assert found == {"RECEIPT_INTEGRITY_EVIDENCE_CONFLICT"}


def test_conflicting_receipt_statuses_fail_closed():
    found = codes(
        [
            contract(),
            integrity(),
            receipt_status("Success"),
            receipt_status("Error"),
            receipt_confirmation(),
        ]
    )
    assert found == {"RECEIPT_EVIDENCE_CONFLICT"}


def test_valid_independent_evidence_verifies_even_when_issuer_flag_is_false():
    assert codes(verified_lifecycle(verified_flag=False)) == set()
    assert all_codes(verified_lifecycle(verified_flag=False)) == set()


def test_issuer_verified_flag_without_external_evidence_is_only_a_claim():
    found = codes(
        [
            contract(),
            integrity(),
            receipt_status(),
            receipt_confirmation(verified_flag=True),
        ]
    )
    assert found == {
        "RECEIPT_RAIL_CONFIRMATION_EVIDENCE_MISSING",
        "RECEIPT_RAIL_VERIFICATION_CLAIM_UNSUPPORTED",
        "RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY",
    }


def test_success_conflicting_with_failed_finality_is_critical():
    findings = verify_independent_receipt_finality_binding(
        [
            contract(),
            finality("failed"),
            rail_confirmation(),
            *valid_receipt(verified_flag=False),
        ]
    )
    finding = next(
        item
        for item in findings
        if item.code == "RECEIPT_SUCCESS_FINALITY_CONFLICT"
    )
    assert finding.severity == "critical"


def test_receipt_and_rail_confirmation_ids_must_match_exactly():
    found = codes(
        [
            contract(),
            finality(),
            rail_confirmation(psp_id="psp-real", network_id="network-real"),
            integrity(),
            receipt_status(),
            receipt_confirmation(
                psp_id="psp-claim",
                network_id="network-claim",
                verified_flag=False,
            ),
            *complete_delivery(),
        ]
    )
    assert found == {"RECEIPT_CONFIRMATION_ID_MISMATCH"}


def test_matching_evidence_arriving_after_receipt_does_not_prove_prior_check():
    found = codes(
        [
            contract(),
            *valid_receipt(verified_flag=True),
            finality(observed_at="2026-08-17T10:00:10Z"),
            rail_confirmation(observed_at="2026-08-17T10:00:11Z"),
            *complete_delivery(),
        ]
    )
    assert found == {
        "RECEIPT_RAIL_CONFIRMATION_EVIDENCE_MISSING",
        "RECEIPT_RAIL_VERIFICATION_CLAIM_UNSUPPORTED",
        "RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY",
        "RECEIPT_VERIFICATION_TIMING_UNPROVEN",
    }


def test_wrong_payment_identity_does_not_satisfy_success_receipt():
    found = codes(
        [
            contract(),
            finality(payment_id="payment-2"),
            rail_confirmation(payment_id="payment-2"),
            *valid_receipt(),
            *complete_delivery(),
        ]
    )
    assert "RECEIPT_SETTLEMENT_IDENTITY_DIVERGENCE" in found
    assert "RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY" in found


def test_same_payment_identity_in_another_operation_is_divergent():
    found = codes(
        [
            contract(),
            finality(operation_id="op-2"),
            rail_confirmation(operation_id="op-2"),
            *valid_receipt(),
        ]
    )
    assert "RECEIPT_SETTLEMENT_IDENTITY_DIVERGENCE" in found
    assert "RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY" in found


def test_receipt_without_typed_payment_identity_remains_unresolved():
    found = codes(
        [
            contract(),
            integrity(payment_id=None, authorization_id=None),
            receipt_status(payment_id=None, authorization_id=None),
            receipt_confirmation(
                payment_id=None,
                authorization_id=None,
            ),
        ]
    )
    assert "RECEIPT_SETTLEMENT_IDENTITY_UNRESOLVED" in found
    assert "RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY" in found


def test_conflicting_authoritative_rail_confirmations_fail_closed():
    found = codes(
        [
            contract(),
            finality(),
            rail_confirmation(psp_id="psp-a", network_id="network-a"),
            rail_confirmation(psp_id="psp-b", network_id="network-b"),
            *valid_receipt(verified_flag=False),
            *complete_delivery(),
        ]
    )
    assert found == {"RECEIPT_RAIL_EVIDENCE_CONFLICT"}


def test_failure_then_settlement_before_receipt_uses_latest_rail_state():
    events = [
        contract(),
        finality("failed", observed_at="2026-08-17T09:59:40Z"),
        finality("settled", observed_at="2026-08-17T09:59:50Z"),
        rail_confirmation(),
        *valid_receipt(),
        *complete_delivery(),
    ]
    assert codes(events) == set()


def test_findings_are_isolated_by_operation():
    bad = [
        contract("op-bad"),
        integrity(operation_id="op-bad", payment_id="payment-bad"),
        receipt_status(operation_id="op-bad", payment_id="payment-bad"),
        receipt_confirmation(
            operation_id="op-bad",
            payment_id="payment-bad",
        ),
    ]
    good = [
        contract("op-good"),
        finality(operation_id="op-good", payment_id="payment-good"),
        rail_confirmation(operation_id="op-good", payment_id="payment-good"),
        integrity(operation_id="op-good", payment_id="payment-good"),
        receipt_status(operation_id="op-good", payment_id="payment-good"),
        receipt_confirmation(
            operation_id="op-good",
            payment_id="payment-good",
            verified_flag=True,
        ),
        *complete_delivery("op-good", "payment-good"),
    ]
    findings = verify_independent_receipt_finality_binding([*bad, *good])
    assert findings
    assert {finding.operation_id for finding in findings} == {"op-bad"}


def test_all_receipt_finality_fixtures_match_oracles():
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
