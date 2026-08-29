from app.astra_origin import normalize_origin
from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def origin_contract(operation_id: str):
    return event(
        Stage.QUOTE_CHALLENGE,
        "requires_credential_origin_binding",
        True,
        "origin-contract",
        operation_id=operation_id,
    )


def challenge_and_binding(operation_id: str, origin: str = "https://merchant.example"):
    return [
        event(
            Stage.QUOTE_CHALLENGE,
            "challenge_origin",
            origin,
            "challenge",
            operation_id=operation_id,
        ),
        event(
            Stage.MANDATE_AUTHORIZATION,
            "credential_bound_origin",
            origin,
            "authorization",
            operation_id=operation_id,
            authorization_id="auth-1",
        ),
    ]


def dispatch(operation_id: str, origin: str, authorization_id: str = "auth-1"):
    return event(
        Stage.PAYMENT_ATTEMPT,
        "credential_dispatch_origin",
        origin,
        "request-log",
        operation_id=operation_id,
        attempt_id="attempt-1",
        authorization_id=authorization_id,
        payment_id=authorization_id,
    )


def test_origin_normalization_handles_case_default_ports_paths_and_ipv6():
    assert normalize_origin("HTTPS://Pay.Example:443/path?q=1#x") == "https://pay.example"
    assert normalize_origin("http://Pay.Example:80/") == "http://pay.example"
    assert normalize_origin("https://pay.example:8443/") == "https://pay.example:8443"
    assert normalize_origin("https://[2001:db8::1]:443/a") == "https://[2001:db8::1]"
    assert normalize_origin("https://user:secret@pay.example/") is None
    assert normalize_origin("not-an-origin") is None


def test_redirect_origin_receiving_challenge_credential_is_detected():
    op = "op-1"
    found = codes(
        [
            origin_contract(op),
            *challenge_and_binding(op),
            dispatch(op, "https://redirect.example"),
            dispatch(op, "https://merchant.example"),
        ]
    )
    assert found == {
        "CROSS_ORIGIN_CREDENTIAL_REUSE",
        "PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE",
    }


def test_direct_retry_to_normalized_challenge_origin_is_verified():
    op = "op-2"
    found = codes(
        [
            origin_contract(op),
            *challenge_and_binding(op, "HTTPS://Merchant.Example:443/paid"),
            dispatch(op, "https://merchant.example"),
        ]
    )
    assert found == set()


def test_authenticated_delegate_origin_is_allowed():
    op = "op-3"
    found = codes(
        [
            origin_contract(op),
            *challenge_and_binding(op),
            event(
                Stage.MANDATE_AUTHORIZATION,
                "authorized_credential_delegate_origin",
                "https://settlement-agent.example",
                "signed-delegation",
                authoritative=True,
                operation_id=op,
            ),
            dispatch(op, "https://settlement-agent.example"),
        ]
    )
    assert found == set()


def test_wrong_authoritative_settlement_consumer_is_critical():
    op = "op-4"
    findings = verify_causal_economic_outcome(
        [
            origin_contract(op),
            *challenge_and_binding(op),
            dispatch(op, "https://merchant.example"),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "credential_consumer_origin",
                "https://redirect.example",
                "chain-attribution",
                authoritative=True,
                operation_id=op,
                authorization_id="auth-1",
            ),
        ]
    )
    finding = next(
        item
        for item in findings
        if item.code == "SETTLEMENT_CONSUMER_ORIGIN_DIVERGENCE"
    )
    assert finding.severity == "critical"


def test_missing_or_malformed_origin_evidence_fails_closed_without_extra_claims():
    op = "op-5"
    found = codes(
        [
            origin_contract(op),
            event(
                Stage.QUOTE_CHALLENGE,
                "challenge_origin",
                "https://merchant.example",
                "challenge",
                operation_id=op,
            ),
            event(
                Stage.MANDATE_AUTHORIZATION,
                "credential_bound_origin",
                "not-an-origin",
                "authorization",
                operation_id=op,
            ),
            dispatch(op, "https://redirect.example"),
        ]
    )
    assert found == {"CREDENTIAL_ORIGIN_EVIDENCE_MISSING"}
