from pathlib import Path

from app.astra_origin import normalize_origin
from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_origin"
EXPECTED_FIXTURES = {
    "mppx_challenge_origin_direct_retry.json",
    "mppx_cross_origin_credential_exposure.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def findings(events):
    return verify_causal_economic_outcome(events)


def codes(events):
    return {finding.code for finding in findings(events)}


def origin_contract(operation_id: str):
    return event(
        Stage.QUOTE_CHALLENGE,
        "requires_credential_origin_binding",
        True,
        "origin-contract",
        operation_id=operation_id,
    )


def challenge_and_binding(
    operation_id: str,
    origin: str = "https://merchant.example",
    authorization_id: str = "auth-1",
):
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
            authorization_id=authorization_id,
        ),
    ]


def dispatch(
    operation_id: str,
    origin: str,
    authorization_id: str | None = "auth-1",
):
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


def delegate(
    operation_id: str,
    origin: str,
    *,
    authoritative: bool = True,
    authorization_id: str | None = None,
):
    return event(
        Stage.MANDATE_AUTHORIZATION,
        "authorized_credential_delegate_origin",
        origin,
        "signed-delegation",
        authoritative=authoritative,
        operation_id=operation_id,
        authorization_id=authorization_id,
    )


def test_origin_normalization_is_strict_and_stable():
    assert normalize_origin("HTTPS://Pay.Example:443/path?q=1#x") == "https://pay.example"
    assert normalize_origin("http://Pay.Example:80/") == "http://pay.example"
    assert normalize_origin("https://pay.example:8443/") == "https://pay.example:8443"
    assert normalize_origin("https://[2001:db8::1]:443/a") == "https://[2001:db8::1]"
    assert normalize_origin("https://BÜCHER.example/") == "https://xn--bcher-kva.example"
    assert normalize_origin("https://user:secret@pay.example/") is None
    assert normalize_origin("ftp://pay.example/") is None
    assert normalize_origin("https://pay.example:99999/") is None
    assert normalize_origin("not-an-origin") is None


def test_redirect_origin_receiving_challenge_credential_is_detected():
    operation_id = "op-1"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
            dispatch(operation_id, "https://redirect.example"),
            dispatch(operation_id, "https://merchant.example"),
        ]
    )

    assert found == {
        "CROSS_ORIGIN_CREDENTIAL_REUSE",
        "PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE",
    }


def test_direct_retry_to_normalized_challenge_origin_is_verified():
    operation_id = "op-2"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(
                operation_id,
                "HTTPS://Merchant.Example:443/paid",
            ),
            dispatch(operation_id, "https://merchant.example"),
        ]
    )

    assert found == set()


def test_authenticated_global_delegate_origin_is_allowed():
    operation_id = "op-3"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
            delegate(operation_id, "https://settlement-agent.example"),
            dispatch(operation_id, "https://settlement-agent.example"),
        ]
    )

    assert found == set()


def test_delegate_for_another_authorization_does_not_leak_authority():
    operation_id = "op-4"
    found = codes(
        [
            origin_contract(operation_id),
            event(
                Stage.QUOTE_CHALLENGE,
                "challenge_origin",
                "https://merchant.example",
                "challenge",
                operation_id=operation_id,
            ),
            event(
                Stage.MANDATE_AUTHORIZATION,
                "credential_bound_origin",
                "https://delegate.example",
                "authorization",
                operation_id=operation_id,
                authorization_id="auth-2",
            ),
            delegate(
                operation_id,
                "https://delegate.example",
                authorization_id="auth-1",
            ),
            dispatch(
                operation_id,
                "https://delegate.example",
                authorization_id="auth-2",
            ),
        ]
    )

    assert found == {
        "AUTHORIZATION_ORIGIN_DIVERGENCE",
        "PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE",
    }


def test_equal_strings_in_different_identity_fields_do_not_authorize_delegate():
    operation_id = "op-cross-type-delegate"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
            delegate(
                operation_id,
                "https://proxy.example",
                authorization_id="shared-id",
            ),
            event(
                Stage.PAYMENT_ATTEMPT,
                "credential_dispatch_origin",
                "https://proxy.example",
                "request-log",
                operation_id=operation_id,
                attempt_id="attempt-cross-type",
                payment_id="shared-id",
            ),
        ]
    )

    assert found == {"PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE"}


def test_equal_strings_in_different_identity_fields_do_not_imply_reuse():
    operation_id = "op-cross-type-reuse"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
            event(
                Stage.PAYMENT_ATTEMPT,
                "credential_dispatch_origin",
                "https://merchant.example",
                "merchant-log",
                operation_id=operation_id,
                attempt_id="attempt-auth",
                authorization_id="shared-id",
            ),
            event(
                Stage.PAYMENT_ATTEMPT,
                "credential_dispatch_origin",
                "https://redirect.example",
                "redirect-log",
                operation_id=operation_id,
                attempt_id="attempt-payment",
                payment_id="shared-id",
            ),
        ]
    )

    assert found == {"PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE"}


def test_non_authoritative_delegate_is_not_permission():
    operation_id = "op-5"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
            delegate(
                operation_id,
                "https://proxy.example",
                authoritative=False,
            ),
            dispatch(operation_id, "https://proxy.example"),
        ]
    )

    assert found == {"PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE"}


def test_wrong_authoritative_settlement_consumer_is_critical():
    operation_id = "op-6"
    result = findings(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
            dispatch(operation_id, "https://merchant.example"),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "credential_consumer_origin",
                "https://redirect.example",
                "settlement-attribution",
                authoritative=True,
                operation_id=operation_id,
                authorization_id="auth-1",
            ),
        ]
    )

    finding = next(
        item
        for item in result
        if item.code == "SETTLEMENT_CONSUMER_ORIGIN_DIVERGENCE"
    )
    assert finding.severity == "critical"


def test_missing_or_malformed_principal_evidence_stops_stronger_claims():
    operation_id = "op-7"
    found = codes(
        [
            origin_contract(operation_id),
            event(
                Stage.QUOTE_CHALLENGE,
                "challenge_origin",
                "https://merchant.example",
                "challenge",
                operation_id=operation_id,
            ),
            event(
                Stage.MANDATE_AUTHORIZATION,
                "credential_bound_origin",
                "not-an-origin",
                "authorization",
                operation_id=operation_id,
            ),
            dispatch(operation_id, "https://redirect.example"),
        ]
    )

    assert found == {"CREDENTIAL_ORIGIN_EVIDENCE_MISSING"}


def test_missing_dispatch_evidence_is_reported_without_inventing_exposure():
    operation_id = "op-8"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
        ]
    )

    assert found == {"CREDENTIAL_DISPATCH_EVIDENCE_MISSING"}


def test_dispatch_without_payment_identity_keeps_reuse_unresolved():
    operation_id = "op-9"
    found = codes(
        [
            origin_contract(operation_id),
            *challenge_and_binding(operation_id),
            dispatch(
                operation_id,
                "https://merchant.example",
                authorization_id=None,
            ),
        ]
    )

    assert found == {"CREDENTIAL_IDENTITY_EVIDENCE_MISSING"}


def test_origin_findings_are_isolated_per_operation():
    bad_operation = "op-bad"
    good_operation = "op-good"
    result = findings(
        [
            origin_contract(bad_operation),
            *challenge_and_binding(bad_operation),
            dispatch(bad_operation, "https://redirect.example"),
            origin_contract(good_operation),
            *challenge_and_binding(good_operation),
            dispatch(good_operation, "https://merchant.example"),
        ]
    )

    origin_findings = [
        item
        for item in result
        if item.code == "PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE"
    ]
    assert len(origin_findings) == 1
    assert origin_findings[0].operation_id == bad_operation


def test_all_origin_fixtures_match_oracles():
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
