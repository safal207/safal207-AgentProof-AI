from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


def _event(stage, key, value, source, **kwargs):
    return StateEvent(
        stage=stage,
        key=key,
        value=value,
        source=source,
        **kwargs,
    )


def test_divergent_settlement_identity_does_not_claim_wrong_origin():
    operation_id = "op-settlement-identity-gate"
    findings = verify_causal_economic_outcome(
        [
            _event(
                Stage.QUOTE_CHALLENGE,
                "requires_credential_origin_binding",
                True,
                "origin-contract",
                operation_id=operation_id,
            ),
            _event(
                Stage.QUOTE_CHALLENGE,
                "challenge_origin",
                "https://merchant.example",
                "challenge",
                operation_id=operation_id,
            ),
            _event(
                Stage.MANDATE_AUTHORIZATION,
                "credential_bound_origin",
                "https://merchant.example",
                "authorization",
                operation_id=operation_id,
                authorization_id="auth-1",
            ),
            _event(
                Stage.PAYMENT_ATTEMPT,
                "credential_dispatch_origin",
                "https://merchant.example",
                "request-log",
                operation_id=operation_id,
                attempt_id="attempt-1",
                authorization_id="auth-1",
            ),
            _event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "credential_consumer_origin",
                "https://redirect.example",
                "settlement-attribution",
                authoritative=True,
                operation_id=operation_id,
                authorization_id="auth-2",
            ),
        ]
    )
    found = {finding.code for finding in findings}

    assert "SETTLEMENT_CREDENTIAL_IDENTITY_DIVERGENCE" in found
    assert "SETTLEMENT_CONSUMER_ORIGIN_DIVERGENCE" not in found
