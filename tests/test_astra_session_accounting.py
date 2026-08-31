from pathlib import Path

from app.astra_session_accounting import verify_payment_session_accounting
from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_session_accounting"
EXPECTED_FIXTURES = {
    "actual_settlement_debit_reconciled.json",
    "agentcore_upto_ceiling_debit_reconciled.json",
    "agentcore_upto_settlement_claimed_as_session_spend.json",
    "settlement_and_session_debit_over_ceiling.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def amount(value, asset="USDC"):
    return {"amount_minor": str(value), "asset": asset}


def codes(events):
    return {finding.code for finding in verify_payment_session_accounting(events)}


def contract(
    *,
    session_id="session-a",
    debit_basis="authorized_ceiling",
    remainder_policy="not_credited",
):
    return event(
        Stage.POLICY_DECISION,
        "payment_session_accounting_contract",
        {
            "required": True,
            "debit_basis": debit_basis,
            "remainder_policy": remainder_policy,
        },
        "accounting-contract",
        session_id=session_id,
    )


def ceiling(
    value=3303,
    *,
    session_id="session-a",
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
    asset="USDC",
):
    return event(
        Stage.MANDATE_AUTHORIZATION,
        "authorized_ceiling_minor",
        amount(value, asset),
        "signed-authorization",
        authoritative=True,
        session_id=session_id,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
    )


def settled_amount(
    value=3003,
    *,
    session_id="session-a",
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
    asset="USDC",
):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "settled_amount_minor",
        amount(value, asset),
        "independent-finality",
        authoritative=True,
        session_id=session_id,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
    )


def debit(
    value=3303,
    *,
    session_id="session-a",
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
    asset="USDC",
):
    return event(
        Stage.RECONCILIATION,
        "session_debit_minor",
        amount(value, asset),
        "session-ledger",
        authoritative=True,
        session_id=session_id,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
    )


def credit(
    value=0,
    *,
    session_id="session-a",
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
    asset="USDC",
):
    return event(
        Stage.RECONCILIATION,
        "session_credit_minor",
        amount(value, asset),
        "session-ledger",
        authoritative=True,
        session_id=session_id,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
    )


def balance(
    key,
    value,
    *,
    stage=Stage.RECONCILIATION,
    session_id="session-a",
    operation_id="op-1",
    authorization_id="auth-1",
    payment_id="payment-1",
    asset="USDC",
):
    return event(
        stage,
        key,
        amount(value, asset),
        "session-ledger",
        authoritative=True,
        session_id=session_id,
        operation_id=operation_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
    )


def claim(key, value, *, asset="USDC"):
    return event(
        Stage.CLAIMED_RESULT,
        key,
        amount(value, asset),
        "application-dashboard",
        session_id="session-a",
        operation_id="op-1",
        authorization_id="auth-1",
        payment_id="payment-1",
    )


def valid_ceiling_debit_flow():
    return [
        contract(),
        ceiling(),
        settled_amount(),
        debit(),
        credit(),
        balance(
            "session_remaining_before_minor",
            50000,
            stage=Stage.POLICY_DECISION,
        ),
        balance("session_remaining_after_minor", 46697),
    ]


def test_accounting_evidence_without_contract_is_unresolved():
    assert codes([debit()]) == {"PAYMENT_SESSION_ACCOUNTING_CONTRACT_MISSING"}


def test_invalid_contract_is_reported():
    invalid = event(
        Stage.POLICY_DECISION,
        "payment_session_accounting_contract",
        {
            "required": True,
            "debit_basis": "wallet_vibes",
            "remainder_policy": "not_credited",
        },
        "accounting-contract",
        session_id="session-a",
    )
    assert codes([invalid]) == {"PAYMENT_SESSION_ACCOUNTING_CONTRACT_INVALID"}


def test_same_scope_contract_conflict_fails_closed():
    assert codes(
        [
            contract(debit_basis="authorized_ceiling"),
            contract(debit_basis="actual_settlement"),
        ]
    ) == {"PAYMENT_SESSION_ACCOUNTING_CONTRACT_CONFLICT"}


def test_global_and_session_contract_cannot_silently_disagree():
    assert codes(
        [
            contract(session_id=None, debit_basis="authorized_ceiling"),
            contract(session_id="session-a", debit_basis="actual_settlement"),
            ceiling(),
        ]
    ) == {"PAYMENT_SESSION_ACCOUNTING_CONTRACT_CONFLICT"}


def test_missing_ceiling_settlement_and_debit_are_separated():
    assert codes([contract()]) == {"AUTHORIZED_CEILING_EVIDENCE_MISSING"}
    assert codes([contract(), ceiling()]) == {
        "ACTUAL_SETTLEMENT_AMOUNT_EVIDENCE_MISSING"
    }
    assert codes([contract(), ceiling(), settled_amount()]) == {
        "SESSION_DEBIT_EVIDENCE_MISSING"
    }


def test_exact_minor_units_reject_float_fraction_and_negative_values():
    invalid_float = event(
        Stage.MANDATE_AUTHORIZATION,
        "authorized_ceiling_minor",
        {"amount_minor": 3303.0, "asset": "USDC"},
        "signed-authorization",
        authoritative=True,
        session_id="session-a",
        operation_id="op-1",
        authorization_id="auth-1",
    )
    invalid_fraction = ceiling(value="3303.5")
    invalid_negative = ceiling(value="-1")

    for invalid in (invalid_float, invalid_fraction, invalid_negative):
        assert codes([contract(), invalid]) == {
            "SESSION_ACCOUNTING_AMOUNT_INVALID"
        }


def test_wrong_asset_is_not_promoted_into_session_truth():
    found = codes(
        [
            contract(),
            ceiling(),
            settled_amount(asset="EURC"),
        ]
    )
    assert "SESSION_ACCOUNTING_ASSET_MISMATCH" in found


def test_wrong_session_and_operation_are_detected():
    wrong_session = codes(
        [
            contract(),
            ceiling(),
            settled_amount(session_id="session-b"),
        ]
    )
    assert "SESSION_ACCOUNTING_SESSION_MISMATCH" in wrong_session

    wrong_operation = codes(
        [
            contract(),
            ceiling(),
            settled_amount(operation_id="op-2"),
        ]
    )
    assert "SESSION_ACCOUNTING_OPERATION_MISMATCH" in wrong_operation


def test_missing_common_typed_identity_stays_unresolved():
    settlement_without_shared_identity = settled_amount(
        authorization_id="auth-2",
        payment_id=None,
    )
    found = codes([contract(), ceiling(), settlement_without_shared_identity])
    assert "SESSION_ACCOUNTING_IDENTITY_UNRESOLVED" in found or (
        "ACTUAL_SETTLEMENT_AMOUNT_EVIDENCE_MISSING" in found
    )


def test_authoritative_amount_conflict_fails_closed():
    found = codes(
        [
            contract(),
            ceiling(),
            settled_amount(3003),
            settled_amount(3004),
        ]
    )
    assert found == {"SESSION_ACCOUNTING_EVIDENCE_CONFLICT"}


def test_ceiling_debit_policy_accepts_settlement_difference_without_credit():
    assert codes(valid_ceiling_debit_flow()) == set()


def test_actual_settlement_debit_policy_accepts_final_amount():
    events = [
        contract(debit_basis="actual_settlement"),
        ceiling(),
        settled_amount(),
        debit(3003),
        credit(),
        balance(
            "session_remaining_before_minor",
            50000,
            stage=Stage.POLICY_DECISION,
        ),
        balance("session_remaining_after_minor", 46997),
    ]
    assert codes(events) == set()


def test_explicit_provider_amount_requires_and_controls_debit():
    provider = event(
        Stage.RECONCILIATION,
        "provider_debit_amount_minor",
        amount(3200),
        "provider-ledger",
        authoritative=True,
        session_id="session-a",
        operation_id="op-1",
        authorization_id="auth-1",
        payment_id="payment-1",
    )
    good = [
        contract(debit_basis="explicit_provider_amount"),
        ceiling(),
        settled_amount(),
        provider,
        debit(3200),
        credit(),
    ]
    assert codes(good) == set()

    missing = [
        contract(debit_basis="explicit_provider_amount"),
        ceiling(),
        settled_amount(),
        debit(3200),
        credit(),
    ]
    assert "EXPLICIT_PROVIDER_AMOUNT_EVIDENCE_MISSING" in codes(missing)


def test_debit_basis_mismatch_is_high_severity():
    findings = verify_payment_session_accounting(
        [
            contract(debit_basis="authorized_ceiling"),
            ceiling(),
            settled_amount(),
            debit(3003),
            credit(),
        ]
    )
    finding = next(
        item for item in findings if item.code == "SESSION_DEBIT_BASIS_MISMATCH"
    )
    assert finding.severity == "high"


def test_over_ceiling_settlement_and_debit_are_critical():
    findings = verify_payment_session_accounting(
        [
            contract(debit_basis="actual_settlement"),
            ceiling(),
            settled_amount(3400),
            debit(3400),
            credit(),
        ]
    )
    by_code = {finding.code: finding for finding in findings}
    assert by_code["SETTLEMENT_EXCEEDS_AUTHORIZED_CEILING"].severity == "critical"
    assert by_code["SESSION_DEBIT_EXCEEDS_AUTHORIZED_CEILING"].severity == "critical"


def test_credited_remainder_requires_the_difference():
    good = [
        contract(remainder_policy="credited"),
        ceiling(),
        settled_amount(),
        debit(),
        credit(300),
        balance(
            "session_remaining_before_minor",
            50000,
            stage=Stage.POLICY_DECISION,
        ),
        balance("session_remaining_after_minor", 46997),
    ]
    assert codes(good) == set()

    bad = [
        contract(remainder_policy="credited"),
        ceiling(),
        settled_amount(),
        debit(),
        credit(0),
    ]
    assert codes(bad) == {"SESSION_REMAINDER_POLICY_MISMATCH"}


def test_external_reconciliation_requires_completed_authoritative_status():
    incomplete = [
        contract(remainder_policy="external_reconciliation"),
        ceiling(),
        settled_amount(),
        debit(),
    ]
    assert codes(incomplete) == {"SESSION_REMAINDER_EVIDENCE_MISSING"}

    completed = [
        *incomplete,
        event(
            Stage.RECONCILIATION,
            "session_remainder_reconciliation_status",
            "complete",
            "external-reconciliation-ledger",
            authoritative=True,
            session_id="session-a",
            operation_id="op-1",
            authorization_id="auth-1",
            payment_id="payment-1",
        ),
    ]
    assert codes(completed) == set()


def test_remaining_balance_arithmetic_is_independent_of_wallet_settlement():
    events = [
        *valid_ceiling_debit_flow()[:-1],
        balance("session_remaining_after_minor", 46997),
    ]
    assert codes(events) == {"SESSION_REMAINING_BALANCE_MISMATCH"}


def test_application_claiming_chain_amount_as_session_spend_is_detected():
    events = [
        *valid_ceiling_debit_flow(),
        claim("claimed_session_spend_minor", 3003),
        claim("claimed_session_remaining_minor", 46997),
    ]
    assert codes(events) == {
        "CLAIMED_SESSION_REMAINING_MISMATCH",
        "CLAIMED_SESSION_SPEND_MISMATCH",
    }


def test_all_accounting_fixtures_match_oracles():
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


def test_full_composed_verifier_exposes_accounting_findings():
    found = {
        finding.code
        for finding in verify_causal_economic_outcome(
            [
                *valid_ceiling_debit_flow(),
                claim("claimed_session_spend_minor", 3003),
            ]
        )
    }
    assert "CLAIMED_SESSION_SPEND_MISMATCH" in found
