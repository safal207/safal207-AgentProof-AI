from pathlib import Path

from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_session"
EXPECTED_FIXTURES = {
    "agentcore_local_pairing_without_binding.json",
    "agentcore_session_bound_reconciled.json",
    "agentcore_session_pair_swapped.json",
    "agentcore_session_reused_cross_operation.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {
        finding.code for finding in verify_causal_economic_outcome(events)
    }


def contract(session_id="session-a", dimensions=None, **kwargs):
    return event(
        Stage.POLICY_DECISION,
        "requires_payment_session_principal_binding",
        {
            "required": True,
            "dimensions": dimensions
            or [
                "user_id",
                "agent_id",
                "payment_instrument_id",
                "merchant_origin",
                "operation_id",
            ],
        },
        "session-contract",
        session_id=session_id,
        **kwargs,
    )


def binding(
    *,
    session_id="session-a",
    operation_id="op-1",
    authoritative=True,
    user_id="user-a",
    agent_id="research-agent",
    payment_instrument_id="instrument-a",
    merchant_origin="https://merchant.example",
):
    return event(
        Stage.POLICY_DECISION,
        "payment_session_binding",
        {
            "user_id": user_id,
            "agent_id": agent_id,
            "payment_instrument_id": payment_instrument_id,
            "merchant_origin": merchant_origin,
            "operation_id": operation_id,
        },
        "session-registry",
        authoritative=authoritative,
        session_id=session_id,
        operation_id=operation_id,
    )


def use(
    *,
    session_id="session-a",
    operation_id="op-1",
    user_id="user-a",
    agent_id="research-agent",
    payment_instrument_id="instrument-a",
    merchant_origin="https://merchant.example/paid",
    payment_id=None,
):
    return event(
        Stage.PAYMENT_ATTEMPT,
        "payment_session_use",
        {
            "user_id": user_id,
            "agent_id": agent_id,
            "payment_instrument_id": payment_instrument_id,
            "merchant_origin": merchant_origin,
            "operation_id": operation_id,
        },
        "attempt-context",
        session_id=session_id,
        operation_id=operation_id,
        attempt_id=f"attempt-{operation_id}",
        payment_id=payment_id,
    )


def settled_finality(
    *,
    session_id="session-a",
    operation_id="op-1",
    payment_id="payment-1",
):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        "settled",
        "independent-finality",
        authoritative=True,
        session_id=session_id,
        operation_id=operation_id,
        attempt_id=f"attempt-{operation_id}",
        payment_id=payment_id,
    )


def completed_outcome(operation_id="op-1", session_id="session-a"):
    return [
        event(
            Stage.RESOURCE_OUTCOME_DELIVERY,
            "delivery_status",
            "delivered",
            "merchant",
            session_id=session_id,
            operation_id=operation_id,
        ),
        event(
            Stage.RECONCILIATION,
            "status",
            "complete",
            "ledger",
            authoritative=True,
            session_id=session_id,
            operation_id=operation_id,
        ),
    ]


def test_missing_authoritative_binding_is_unresolved():
    found = codes([contract(), use()])
    assert found == {"PAYMENT_SESSION_BINDING_MISSING"}


def test_non_authoritative_binding_does_not_grant_principal_authority():
    found = codes([contract(), binding(authoritative=False), use()])
    assert found == {"PAYMENT_SESSION_BINDING_MISSING"}


def test_matching_principals_and_normalized_merchant_have_no_findings():
    found = codes(
        [
            contract(),
            binding(merchant_origin="HTTPS://Merchant.Example:443/catalog"),
            use(merchant_origin="https://merchant.example/paid?item=1"),
        ]
    )
    assert found == set()


def test_only_declared_dimensions_are_enforced():
    found = codes(
        [
            contract(dimensions=["user_id", "operation_id"]),
            binding(),
            use(
                agent_id="another-agent",
                payment_instrument_id="another-instrument",
                merchant_origin="https://another.example",
            ),
        ]
    )
    assert found == set()


def test_malformed_binding_merchant_is_incomplete_not_a_crossover():
    found = codes(
        [
            contract(dimensions=["merchant_origin"]),
            binding(merchant_origin="not-an-origin"),
            use(),
        ]
    )
    assert found == {"PAYMENT_SESSION_BINDING_INCOMPLETE"}


def test_all_principal_mismatches_are_separated():
    found = codes(
        [
            contract(),
            binding(),
            use(),
            use(
                operation_id="op-2",
                user_id="user-b",
                agent_id="discovery-agent",
                payment_instrument_id="instrument-b",
                merchant_origin="https://other.example",
            ),
        ]
    )
    assert found == {
        "SESSION_AGENT_CROSSOVER",
        "SESSION_ID_REUSED_ACROSS_OPERATIONS",
        "SESSION_INSTRUMENT_CROSSOVER",
        "SESSION_MERCHANT_CROSSOVER",
        "SESSION_OPERATION_CROSSOVER",
        "SESSION_USER_CROSSOVER",
    }


def test_conflicting_contract_dimensions_fail_closed():
    found = codes(
        [
            contract(dimensions=["agent_id"]),
            contract(dimensions=["user_id"]),
            binding(),
            use(),
        ]
    )
    assert found == {"PAYMENT_SESSION_CONTRACT_CONFLICT"}


def test_conflicting_authoritative_bindings_fail_closed():
    found = codes(
        [
            contract(dimensions=["agent_id"]),
            binding(agent_id="research-agent"),
            binding(agent_id="discovery-agent"),
            use(),
        ]
    )
    assert found == {"PAYMENT_SESSION_BINDING_CONFLICT"}


def test_authoritative_binding_without_use_is_unresolved():
    found = codes([contract(), binding()])
    assert found == {"PAYMENT_SESSION_USE_EVIDENCE_MISSING"}


def test_missing_use_dimension_is_unresolved_without_inventing_value():
    incomplete_use = event(
        Stage.PAYMENT_ATTEMPT,
        "payment_session_use",
        {"user_id": "user-a"},
        "attempt-context",
        session_id="session-a",
        operation_id="op-1",
    )
    found = codes([contract(), binding(), incomplete_use])
    assert found == {"SESSION_USE_BINDING_UNRESOLVED"}


def test_operation_scoped_session_reuse_is_detected():
    found = codes(
        [
            contract(dimensions=["operation_id"]),
            binding(),
            use(),
            use(operation_id="op-2"),
        ]
    )
    assert found == {
        "SESSION_ID_REUSED_ACROSS_OPERATIONS",
        "SESSION_OPERATION_CROSSOVER",
    }


def test_matching_payment_settled_under_another_session_is_critical():
    found = codes(
        [
            contract(),
            binding(),
            use(payment_id="payment-1"),
            settled_finality(session_id="session-b"),
            *completed_outcome(),
        ]
    )
    assert found == {"SETTLEMENT_SESSION_CROSSOVER"}


def test_settlement_without_session_attribution_is_unresolved():
    found = codes(
        [
            contract(),
            binding(),
            use(payment_id="payment-1"),
            settled_finality(session_id=None),
            *completed_outcome(),
        ]
    )
    assert found == {"SETTLEMENT_SESSION_BINDING_UNRESOLVED"}


def test_unrelated_settlement_does_not_create_session_claim():
    found = codes(
        [
            contract(),
            binding(),
            use(payment_id="payment-1"),
            settled_finality(session_id="session-b", payment_id="payment-2"),
            *completed_outcome(),
        ]
    )
    assert found == set()


def test_global_contract_rejects_use_without_session_identity():
    global_contract = contract(session_id=None, dimensions=["user_id"])
    unscoped_use = event(
        Stage.PAYMENT_ATTEMPT,
        "payment_session_use",
        {"user_id": "user-a"},
        "attempt-context",
    )
    found = codes([global_contract, unscoped_use])
    assert found == {
        "PAYMENT_SESSION_BINDING_MISSING",
        "SESSION_USE_BINDING_UNRESOLVED",
    }


def test_invalid_contract_dimension_is_reported():
    invalid = event(
        Stage.POLICY_DECISION,
        "requires_payment_session_principal_binding",
        {"required": True, "dimensions": ["wallet_vibes"]},
        "session-contract",
        session_id="session-a",
    )
    found = codes([invalid])
    assert found == {"PAYMENT_SESSION_CONTRACT_INVALID"}


def test_findings_are_isolated_by_session():
    result = verify_causal_economic_outcome(
        [
            contract(session_id="session-a", dimensions=["agent_id"]),
            binding(session_id="session-a"),
            use(session_id="session-a", agent_id="discovery-agent"),
            contract(session_id="session-b", dimensions=["agent_id"]),
            binding(
                session_id="session-b",
                operation_id="op-2",
                agent_id="discovery-agent",
            ),
            use(
                session_id="session-b",
                operation_id="op-2",
                agent_id="discovery-agent",
            ),
        ]
    )
    crossovers = [
        finding
        for finding in result
        if finding.code == "SESSION_AGENT_CROSSOVER"
    ]
    assert len(crossovers) == 1
    assert crossovers[0].operation_id == "op-1"


def test_all_session_fixtures_match_oracles():
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
