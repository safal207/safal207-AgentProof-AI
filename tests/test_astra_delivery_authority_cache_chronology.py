from app.astra_delivery_authority import verify_finality_bound_delivery_authority
from app.astra_spider import Stage, StateEvent


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {
        finding.code for finding in verify_finality_bound_delivery_authority(events)
    }


def prefix():
    identity = {
        "operation_id": "op-cache",
        "authorization_id": "auth-cache",
        "payment_id": "auth-cache",
    }
    return identity, [
        event(
            Stage.POLICY_DECISION,
            "requires_finality_bound_delivery_authority",
            {
                "required": True,
                "verification_not_delivery_authority": True,
            },
            "contract",
            operation_id="op-cache",
        ),
        event(
            Stage.PAYMENT_ATTEMPT,
            "attempt",
            "initial_attempt",
            "payment-handler",
            attempt_id="attempt-initial",
            **identity,
        ),
        event(
            Stage.CLAIMED_RESULT,
            "payment_verification_status",
            "verified",
            "facilitator-verify",
            attempt_id="attempt-initial",
            **identity,
        ),
        event(
            Stage.ACTUAL_SETTLEMENT_FINALITY,
            "payment_status",
            "failed",
            "independent-finality",
            authoritative=True,
            attempt_id="attempt-initial",
            **identity,
        ),
    ]


def cache(status, identity):
    return event(
        Stage.RECONCILIATION,
        "admission_cache_status",
        status,
        "idempotency-cache",
        authoritative=True,
        **identity,
    )


def replay(identity):
    return event(
        Stage.PAYMENT_ATTEMPT,
        "attempt",
        "replay_same_authorization",
        "client",
        attempt_id="attempt-replay",
        **identity,
    )


def basis(identity):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_authority_basis",
        "verification_cache",
        "delivery-gate",
        attempt_id="attempt-replay",
        **identity,
    )


def delivery(identity):
    return event(
        Stage.RESOURCE_OUTCOME_DELIVERY,
        "delivery_status",
        "delivered",
        "merchant",
        attempt_id="attempt-replay",
        **identity,
    )


def test_revocation_recorded_after_replay_does_not_erase_active_cache_at_reuse():
    identity, events = prefix()
    events.extend(
        [
            cache("active", identity),
            replay(identity),
            basis(identity),
            delivery(identity),
            cache("revoked", identity),
        ]
    )

    assert codes(events) == {
        "REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
        "VERIFICATION_CACHE_SURVIVES_SETTLEMENT_FAILURE",
        "VERIFICATION_USED_AS_DELIVERY_AUTHORITY",
    }


def test_revocation_before_replay_closes_the_cache_boundary():
    identity, events = prefix()
    events.extend(
        [
            cache("active", identity),
            cache("revoked", identity),
            replay(identity),
        ]
    )

    assert codes(events) == set()


def test_settlement_before_replay_closes_failure_without_cache_evidence():
    identity, events = prefix()
    events.extend(
        [
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                "corrected-finality",
                authoritative=True,
                attempt_id="attempt-recovery",
                **identity,
            ),
            replay(identity),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "delivery_authority_basis",
                "settlement_finality",
                "delivery-gate",
                attempt_id="attempt-replay",
                **identity,
            ),
            delivery(identity),
        ]
    )

    assert codes(events) == set()
