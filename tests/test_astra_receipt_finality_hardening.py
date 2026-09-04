from app.astra_receipt_finality import verify_independent_receipt_finality_binding
from app.astra_spider import Stage, StateEvent


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {
        finding.code
        for finding in verify_independent_receipt_finality_binding(events)
    }


def contract(operation_id="op-1", *, valid=True):
    value = {
        "required": True,
        "success_requires_settled_finality": True,
        "verified_flag_is_claim_only": True,
    }
    if not valid:
        value["verified_flag_is_claim_only"] = False
    return event(
        Stage.POLICY_DECISION,
        "requires_independent_receipt_finality_binding",
        value,
        "receipt-finality-contract",
        operation_id=operation_id,
    )


def status(
    value="Success",
    *,
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
    observed_at="2026-08-17T10:00:00Z",
):
    return event(
        Stage.RECEIPT,
        "receipt_status",
        value,
        "payment-receipt",
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
        observed_at=observed_at,
    )


def integrity(
    *,
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
):
    return event(
        Stage.RECEIPT,
        "receipt_integrity_status",
        "verified",
        "independent-jws-verifier",
        authoritative=True,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
    )


def receipt_confirmation(
    *,
    psp_id="psp-1",
    network_id="network-1",
    verified=False,
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
):
    return event(
        Stage.RECEIPT,
        "receipt_rail_confirmation",
        {
            "psp_confirmation_id": psp_id,
            "network_confirmation_id": network_id,
            "rail_confirmation_verified": verified,
        },
        "payment-receipt",
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
    )


def finality(
    value,
    *,
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
    observed_at="2026-08-17T09:59:50Z",
):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        value,
        "independent-rail-resolver",
        authoritative=True,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
        observed_at=observed_at,
    )


def rail_confirmation(
    *,
    psp_id="psp-1",
    network_id="network-1",
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
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
        authorization_id=authorization_id,
        payment_id=payment_id,
        observed_at=observed_at,
    )


def test_integrity_for_conflicting_payment_identity_is_not_reused():
    found = codes(
        [
            contract(),
            finality("settled"),
            rail_confirmation(),
            status(),
            integrity(payment_id="payment-2"),
            receipt_confirmation(),
        ]
    )

    assert found == {"RECEIPT_EVIDENCE_CONFLICT"}


def test_confirmation_claim_for_conflicting_payment_identity_is_not_reused():
    found = codes(
        [
            contract(),
            finality("settled"),
            rail_confirmation(),
            status(),
            integrity(),
            receipt_confirmation(payment_id="payment-2"),
        ]
    )

    assert found == {"RECEIPT_EVIDENCE_CONFLICT"}


def test_unrecognized_duplicate_status_fails_closed_regardless_of_order():
    events = [
        contract(),
        integrity(),
        status("Success"),
        status("pending"),
        receipt_confirmation(),
    ]
    assert codes(events) == {"RECEIPT_STATUS_EVIDENCE_INVALID"}
    assert codes([events[0], events[1], events[3], events[2], events[4]]) == {
        "RECEIPT_STATUS_EVIDENCE_INVALID"
    }


def test_failed_finality_after_receipt_still_contradicts_success():
    found = codes(
        [
            contract(),
            status(),
            integrity(),
            receipt_confirmation(),
            finality(
                "failed",
                observed_at="2026-08-17T10:00:10Z",
            ),
            rail_confirmation(observed_at="2026-08-17T10:00:11Z"),
        ]
    )

    assert "RECEIPT_SUCCESS_FINALITY_CONFLICT" in found
    assert "RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY" not in found


def test_late_confirmation_id_mismatch_is_reported_as_contradiction():
    found = codes(
        [
            contract(),
            status(),
            integrity(),
            receipt_confirmation(
                psp_id="psp-claimed",
                network_id="network-claimed",
                verified=True,
            ),
            finality(
                "settled",
                observed_at="2026-08-17T10:00:10Z",
            ),
            rail_confirmation(
                psp_id="psp-actual",
                network_id="network-actual",
                observed_at="2026-08-17T10:00:11Z",
            ),
        ]
    )

    assert "RECEIPT_CONFIRMATION_ID_MISMATCH" in found
    assert "RECEIPT_RAIL_VERIFICATION_CLAIM_UNSUPPORTED" in found


def test_invalid_global_contract_blocks_clean_operation_specific_contract():
    found = codes(
        [
            contract(operation_id=None, valid=False),
            contract(operation_id="op-1"),
            finality("settled"),
            rail_confirmation(),
            status(),
            integrity(),
            receipt_confirmation(verified=True),
        ]
    )

    assert found == {"RECEIPT_FINALITY_BINDING_CONTRACT_INVALID"}


def test_matching_rail_record_cannot_mask_second_conflicting_record():
    found = codes(
        [
            contract(),
            finality("settled"),
            finality(
                "settled",
                payment_id="payment-2",
            ),
            rail_confirmation(),
            status(),
            integrity(),
            receipt_confirmation(verified=True),
        ]
    )

    assert "RECEIPT_SETTLEMENT_IDENTITY_DIVERGENCE" in found
    assert "RECEIPT_RAIL_VERIFICATION_CLAIM_UNSUPPORTED" in found
