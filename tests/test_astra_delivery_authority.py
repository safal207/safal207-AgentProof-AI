from pathlib import Path

from app.astra_delivery_authority import verify_finality_bound_delivery_authority
from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_delivery_authority"
EXPECTED_FIXTURES = {
    "x402_verified_finality_unknown.json",
    "x402toll_failed_settlement_recovery.json",
    "x402toll_verification_cache_free_delivery.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def specialized_codes(events):
    return {
        finding.code for finding in verify_finality_bound_delivery_authority(events)
    }


def all_codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def contract(operation_id="op-1", *, allow_entitlement=False):
    return event(
        Stage.POLICY_DECISION,
        "requires_finality_bound_delivery_authority",
        {
            "required": True,
            "verification_not_delivery_authority": True,
            "allow_non_payment_entitlement": allow_entitlement,
        },
        "delivery-authority-contract",
        operation_id=operation_id,
    )


def attempt(
    operation_id="op-1",
    authorization_id="auth-1",
    attempt_id="attempt-1",
    value="initial_attempt",
):
    return event(
        Stage.PAYMENT_ATTEMPT,
        "attempt",
        value,
        "payment-handler",
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=authorization_id,
    )


def verification(
    operation_id="op-1",
    authorization_id="auth-1",
    attempt_id="attempt-1",
):
    return event(
        Stage.CLAIMED_RESULT,
        "payment_verification_status",
        "verified",
        "facilitator-verify",
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=authorization_id,
    )


def finality(
    status,
    operation_id="op-1",
    authorization_id="auth-1",
    attempt_id="attempt-1",
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
        payment_id=authorization_id,
    )


def cache(
    status,
    operation_id="op-1",
    authorization_id="auth-1",
):
    return event(
        Stage.RECONCILIATION,
        "admission_cache_status",
        status,
        "idempotency-cache",
        authoritative=True,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=authorization_id,
    )


def replay(
    operation_id="op-1",
    authorization_id="auth-1",
    attempt_id="attempt-2",
):
    return attempt(
        operation_id=operation_id,
        authorization_id=authorization_id,
        attempt_id=attempt_id,
        value="replay_same_authorization",
    )


def basis(
    value,
    operation_id="op-1",
    authorization_id="auth-1",
    attempt_id="attempt-2",
):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_authority_basis",
        value,
        "delivery-gate",
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=authorization_id,
    )


def delivery(
    operation_id="op-1",
    authorization_id="auth-1",
    attempt_id="attempt-2",
):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_status",
        "delivered",
        "merchant-response",
        operation_id=operation_id,
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=authorization_id,
    )


def entitlement(operation_id="op-1"):
    return event(
        Stage.POLICY_DECISION,
        "non_payment_entitlement_status",
        "granted",
        "authoritative-entitlement-registry",
        authoritative=True,
        operation_id=operation_id,
    )


def failed_flow(cache_status="revoked", operation_id="op-1"):
    return [
        contract(operation_id),
        attempt(operation_id),
        verification(operation_id),
        finality("failed", operation_id),
        cache(cache_status, operation_id),
    ]


def test_invalid_contract_is_unresolved():
    invalid = event(
        Stage.POLICY_DECISION,
        "requires_finality_bound_delivery_authority",
        {"required": True},
        "delivery-authority-contract",
        operation_id="op-invalid",
    )
    assert specialized_codes([invalid]) == {
        "FINALITY_BOUND_DELIVERY_CONTRACT_INVALID"
    }


def test_required_contract_without_verification_evidence_is_unresolved():
    assert specialized_codes([contract()]) == {
        "PAYMENT_VERIFICATION_EVIDENCE_MISSING"
    }


def test_verified_payment_without_finality_is_unresolved():
    assert specialized_codes([contract(), attempt(), verification()]) == {
        "SETTLEMENT_FINALITY_EVIDENCE_MISSING"
    }


def test_failed_settlement_without_cache_evidence_is_unresolved():
    assert specialized_codes(
        [contract(), attempt(), verification(), finality("failed")]
    ) == {"VERIFICATION_CACHE_STATUS_MISSING"}


def test_active_cache_after_failed_settlement_is_high_severity():
    findings = verify_finality_bound_delivery_authority(failed_flow("active"))
    finding = next(
        item
        for item in findings
        if item.code == "VERIFICATION_CACHE_SURVIVES_SETTLEMENT_FAILURE"
    )
    assert finding.severity == "high"


def test_revoked_cache_and_denied_replay_do_not_create_a_finding():
    events = [
        *failed_flow("revoked"),
        replay(),
        event(
            Stage.CLAIMED_RESULT,
            "replay_status",
            "denied",
            "merchant-regression-guard",
            operation_id="op-1",
            attempt_id="attempt-2",
            authorization_id="auth-1",
            payment_id="auth-1",
        ),
    ]
    assert specialized_codes(events) == set()


def test_same_credential_replayed_to_delivery_is_detected():
    events = [
        *failed_flow("active"),
        replay(),
        basis("verification_cache"),
        event(
            Stage.RECEIPT,
            "response_provenance_status",
            "verified",
            "response-hash-verifier",
            operation_id="op-1",
            attempt_id="attempt-2",
            authorization_id="auth-1",
            payment_id="auth-1",
        ),
        delivery(),
    ]

    assert specialized_codes(events) == {
        "REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
        "VERIFICATION_CACHE_SURVIVES_SETTLEMENT_FAILURE",
        "VERIFICATION_USED_AS_DELIVERY_AUTHORITY",
    }
    assert "DELIVERED_BUT_NOT_SETTLED" in all_codes(events)


def test_valid_response_provenance_does_not_supply_payment_authority():
    events = [
        *failed_flow("revoked"),
        replay(),
        event(
            Stage.RECEIPT,
            "response_provenance_status",
            "verified",
            "response-hash-verifier",
            operation_id="op-1",
            attempt_id="attempt-2",
            authorization_id="auth-1",
            payment_id="auth-1",
        ),
        delivery(),
    ]

    assert specialized_codes(events) == {
        "DELIVERY_AUTHORITY_BASIS_MISSING",
        "REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
    }


def test_claimed_settlement_basis_requires_matching_finality_before_delivery():
    events = [
        *failed_flow("revoked"),
        replay(),
        basis("settlement_finality"),
        delivery(),
    ]

    assert specialized_codes(events) == {
        "DELIVERY_AUTHORITY_FINALITY_UNRESOLVED",
        "REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
    }


def test_matching_settlement_after_replay_authorizes_delivery():
    events = [
        *failed_flow("revoked"),
        replay(),
        finality(
            "settled",
            authorization_id="auth-1",
            attempt_id="attempt-2",
        ),
        basis("settlement_finality"),
        delivery(),
    ]

    assert specialized_codes(events) == set()


def test_different_authorization_is_not_mislabeled_as_replay():
    events = [
        *failed_flow("revoked"),
        attempt(
            authorization_id="auth-2",
            attempt_id="attempt-fresh",
            value="fresh_authorization_attempt",
        ),
        finality(
            "settled",
            authorization_id="auth-2",
            attempt_id="attempt-fresh",
        ),
        basis(
            "settlement_finality",
            authorization_id="auth-2",
            attempt_id="attempt-fresh",
        ),
        delivery(
            authorization_id="auth-2",
            attempt_id="attempt-fresh",
        ),
    ]

    assert specialized_codes(events) == set()


def test_replay_like_attempt_without_identity_is_unresolved():
    replay_without_identity = event(
        Stage.PAYMENT_ATTEMPT,
        "attempt",
        "replay_same_authorization",
        "client",
        operation_id="op-1",
        attempt_id="attempt-unknown",
    )
    events = [*failed_flow("revoked"), replay_without_identity]

    assert specialized_codes(events) == {
        "REPLAY_PAYMENT_IDENTITY_UNRESOLVED"
    }


def test_authoritative_non_payment_entitlement_can_separately_authorize_delivery():
    events = [
        contract(allow_entitlement=True),
        attempt(),
        verification(),
        finality("failed"),
        cache("revoked"),
        replay(),
        entitlement(),
        basis("non_payment_entitlement"),
        delivery(),
    ]

    assert specialized_codes(events) == set()


def test_non_payment_entitlement_must_be_enabled_and_authoritative():
    unauthoritative_entitlement = event(
        Stage.POLICY_DECISION,
        "non_payment_entitlement_status",
        "granted",
        "application-claim",
        authoritative=False,
        operation_id="op-1",
    )
    events = [
        *failed_flow("revoked"),
        replay(),
        unauthoritative_entitlement,
        basis("non_payment_entitlement"),
        delivery(),
    ]

    assert specialized_codes(events) == {
        "DELIVERY_AUTHORITY_FINALITY_UNRESOLVED",
        "REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
    }


def test_findings_are_isolated_by_operation():
    bad = [
        *failed_flow("active", operation_id="op-bad"),
        replay(operation_id="op-bad"),
        basis("verification_cache", operation_id="op-bad"),
        delivery(operation_id="op-bad"),
    ]
    good = [
        *failed_flow("revoked", operation_id="op-good"),
        replay(operation_id="op-good"),
        event(
            Stage.CLAIMED_RESULT,
            "replay_status",
            "denied",
            "merchant-regression-guard",
            operation_id="op-good",
            attempt_id="attempt-2",
            authorization_id="auth-1",
            payment_id="auth-1",
        ),
    ]

    findings = verify_finality_bound_delivery_authority([*bad, *good])
    assert findings
    assert {finding.operation_id for finding in findings} == {"op-bad"}


def test_all_delivery_authority_fixtures_match_oracles():
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
