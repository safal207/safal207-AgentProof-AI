from app.astra_delivery_authority import verify_finality_bound_delivery_authority
from app.astra_spider import Stage, StateEvent


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {
        finding.code for finding in verify_finality_bound_delivery_authority(events)
    }


def contract(*, allow_entitlement=False):
    return event(
        Stage.POLICY_DECISION,
        "requires_finality_bound_delivery_authority",
        {
            "required": True,
            "verification_not_delivery_authority": True,
            "allow_non_payment_entitlement": allow_entitlement,
        },
        "delivery-authority-contract",
        operation_id="op-1",
    )


def attempt(value, *, attempt_id, authorization_id="auth-1"):
    return event(
        Stage.PAYMENT_ATTEMPT,
        "attempt",
        value,
        "payment-handler",
        operation_id="op-1",
        attempt_id=attempt_id,
        authorization_id=authorization_id,
        payment_id=authorization_id,
    )


def verification():
    return event(
        Stage.CLAIMED_RESULT,
        "payment_verification_status",
        "verified",
        "facilitator-verify",
        operation_id="op-1",
        attempt_id="attempt-initial",
        authorization_id="auth-1",
        payment_id="auth-1",
    )


def finality(status, *, attempt_id, source="independent-finality"):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        status,
        source,
        authoritative=True,
        operation_id="op-1",
        attempt_id=attempt_id,
        authorization_id="auth-1",
        payment_id="auth-1",
    )


def cache(status, *, authoritative=True):
    return event(
        Stage.RECONCILIATION,
        "admission_cache_status",
        status,
        "idempotency-cache",
        authoritative=authoritative,
        operation_id="op-1",
        authorization_id="auth-1",
        payment_id="auth-1",
    )


def basis(value):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_authority_basis",
        value,
        "delivery-gate",
        operation_id="op-1",
        attempt_id="attempt-replay",
        authorization_id="auth-1",
        payment_id="auth-1",
    )


def delivery():
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_status",
        "delivered",
        "merchant-response",
        operation_id="op-1",
        attempt_id="attempt-replay",
        authorization_id="auth-1",
        payment_id="auth-1",
    )


def entitlement():
    return event(
        Stage.POLICY_DECISION,
        "non_payment_entitlement_status",
        "granted",
        "entitlement-registry",
        authoritative=True,
        operation_id="op-1",
    )


def failure_prefix(*, cache_event=None, contracts=None):
    return [
        *(contracts or [contract()]),
        attempt("initial_attempt", attempt_id="attempt-initial"),
        verification(),
        finality("failed", attempt_id="attempt-initial"),
        *( [cache_event] if cache_event is not None else [] ),
    ]


def test_late_settlement_after_delivery_does_not_retroactively_authorize_it():
    events = [
        *failure_prefix(cache_event=cache("revoked")),
        attempt("replay_same_authorization", attempt_id="attempt-replay"),
        basis("settlement_finality"),
        delivery(),
        finality(
            "settled",
            attempt_id="attempt-replay",
            source="late-chain-finality",
        ),
    ]

    assert codes(events) == {
        "DELIVERY_AUTHORITY_FINALITY_UNRESOLVED",
        "REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
    }


def test_settlement_before_replay_can_authorize_idempotent_delivery():
    events = [
        *failure_prefix(cache_event=cache("revoked")),
        finality(
            "settled",
            attempt_id="attempt-recovery",
            source="corrected-chain-finality",
        ),
        attempt("replay_same_authorization", attempt_id="attempt-replay"),
        basis("settlement_finality"),
        delivery(),
    ]

    assert codes(events) == set()


def test_later_entitlement_opt_in_cannot_weaken_an_earlier_strict_contract():
    events = [
        *failure_prefix(
            cache_event=cache("revoked"),
            contracts=[
                contract(allow_entitlement=False),
                contract(allow_entitlement=True),
            ],
        ),
        attempt("replay_same_authorization", attempt_id="attempt-replay"),
        entitlement(),
        basis("non_payment_entitlement"),
        delivery(),
    ]

    assert codes(events) == {
        "DELIVERY_AUTHORITY_FINALITY_UNRESOLVED",
        "REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
    }


def test_non_authoritative_cache_revocation_cannot_close_the_failure_boundary():
    events = failure_prefix(
        cache_event=cache("revoked", authoritative=False),
    )

    assert codes(events) == {"VERIFICATION_CACHE_STATUS_MISSING"}


def test_unrecognized_authoritative_cache_state_remains_unresolved():
    events = failure_prefix(cache_event=cache("maybe_revoked"))

    assert codes(events) == {"VERIFICATION_CACHE_STATUS_MISSING"}
