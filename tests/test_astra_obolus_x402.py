from pathlib import Path

from app.astra_trace import build_trace_report, load_trace
from app.astra_verifier import Stage


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra"


def _load(name: str):
    trace = load_trace(FIXTURES / name)
    return trace, build_trace_report(trace)


def test_obolus_new_nonce_retry_is_two_real_payments_for_one_obligation():
    trace, report = _load("obolus_x402_new_nonce_double_settlement.json")

    assert report.verdict == "DIVERGED"
    assert {finding.code for finding in report.findings} == {
        "CLAIMED_FAILED_BUT_SETTLED",
        "FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT",
        "OVER_CAPTURE",
        "RETRY_DUPLICATE_PAYMENT",
    }

    attempts = [
        event
        for event in trace.events
        if event.stage == Stage.PAYMENT_ATTEMPT and event.key == "attempt"
    ]
    assert {event.operation_id for event in attempts} == {"obolus-inference-001"}
    assert {event.authorization_id for event in attempts} == {
        "eip3009-nonce-a",
        "eip3009-nonce-b",
    }

    settlements = [
        event
        for event in trace.events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key == "payment_status"
        and event.value == "settled"
        and event.authoritative
    ]
    assert {event.payment_id for event in settlements} == {"tx-a", "tx-b"}


def test_obolus_safe_recovery_preserves_authorization_and_one_settlement():
    trace, report = _load("obolus_x402_same_authorization_reconciled.json")

    assert report.verdict == "VERIFIED"
    assert report.findings == ()

    attempts = [
        event
        for event in trace.events
        if event.stage == Stage.PAYMENT_ATTEMPT and event.key == "attempt"
    ]
    assert {event.operation_id for event in attempts} == {"obolus-inference-002"}
    assert {event.authorization_id for event in attempts} == {"eip3009-nonce-c"}
    assert {event.payload_hash for event in attempts} == {"sha256:payload-c"}

    settlements = [
        event
        for event in trace.events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key == "payment_status"
        and event.value == "settled"
        and event.authoritative
    ]
    assert len(settlements) == 1
    assert settlements[0].payment_id == "tx-c"
