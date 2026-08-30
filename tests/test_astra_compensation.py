from dataclasses import replace
from pathlib import Path

from app.astra_compensation import is_fully_compensated
from app.astra_spider import Finding, Stage, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_compensation"
EXPECTED_FIXTURES = {
    "full_refund_compensated.json",
    "mixed_operations_diverged.json",
    "partial_refund_diverged.json",
    "refund_without_terminal_reconciliation.json",
    "unbound_refund_diverged.json",
    "wrong_asset_diverged.json",
}


def fixture(name: str):
    return load_trace(FIXTURES / name)


def test_all_compensation_fixtures_match_their_oracles():
    paths = sorted(FIXTURES.glob("*.json"))
    assert {path.name for path in paths} == EXPECTED_FIXTURES

    for path in paths:
        trace = load_trace(path)
        report = build_trace_report(trace)
        assert {finding.code for finding in report.findings} == set(trace.expected_codes)
        assert report.verdict == trace.expected_verdict
        assert len(report.evidence_hash) == 64


def test_compensated_report_preserves_historical_failure_findings():
    report = build_trace_report(fixture("full_refund_compensated.json"))

    assert report.verdict == "COMPENSATED"
    assert {finding.code for finding in report.findings} == {
        "CLAIMED_FAILED_BUT_SETTLED",
        "SETTLED_BUT_NOT_DELIVERED",
        "SETTLED_FULFILLMENT_FAILED",
    }
    assert all(finding.operation_id == "comp-op-001" for finding in report.findings)


def test_compensated_is_an_accepted_fixture_verdict():
    trace = fixture("full_refund_compensated.json")
    assert trace.expected_verdict == "COMPENSATED"


def test_partial_refund_remains_diverged():
    assert build_trace_report(fixture("partial_refund_diverged.json")).verdict == "DIVERGED"


def test_wrong_asset_remains_diverged():
    assert build_trace_report(fixture("wrong_asset_diverged.json")).verdict == "DIVERGED"


def test_unbound_refund_remains_diverged():
    report = build_trace_report(fixture("unbound_refund_diverged.json"))
    assert report.verdict == "DIVERGED"
    assert "REFUND_OPERATION_BINDING_UNRESOLVED" in {
        finding.code for finding in report.findings
    }


def test_terminal_reconciliation_is_required():
    assert (
        build_trace_report(
            fixture("refund_without_terminal_reconciliation.json")
        ).verdict
        == "DIVERGED"
    )


def test_every_divergent_operation_must_be_compensated():
    assert build_trace_report(fixture("mixed_operations_diverged.json")).verdict == "DIVERGED"


def test_refund_evidence_before_original_settlement_cannot_compensate():
    trace = fixture("full_refund_compensated.json")
    events = list(trace.events)
    refund_events = [
        event
        for event in events
        if event.payment_id == "refund-pay-001"
        and event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
    ]
    remaining = [event for event in events if event not in refund_events]
    first_finality = next(
        index
        for index, event in enumerate(remaining)
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
    )
    reordered = tuple(
        remaining[:first_finality]
        + refund_events
        + remaining[first_finality:]
    )

    report = build_trace_report(replace(trace, events=reordered, expected_verdict=None))
    assert report.verdict == "DIVERGED"


def test_terminal_reconciliation_before_binding_cannot_compensate():
    trace = fixture("full_refund_compensated.json")
    events = list(trace.events)
    reconciliation = next(
        event
        for event in events
        if event.stage == Stage.RECONCILIATION and event.key == "status"
    )
    events.remove(reconciliation)
    binding_index = next(
        index
        for index, event in enumerate(events)
        if event.stage == Stage.RECONCILIATION
        and event.key == "refund_operation_binding"
    )
    events.insert(binding_index, reconciliation)

    report = build_trace_report(
        replace(trace, events=tuple(events), expected_verdict=None)
    )
    assert report.verdict == "DIVERGED"


def test_noncompensable_high_finding_blocks_compensated_verdict():
    trace = fixture("full_refund_compensated.json")
    findings = list(verify_causal_economic_outcome(trace.events))
    findings.append(
        Finding(
            code="OVER_CAPTURE",
            from_stage=Stage.MANDATE_AUTHORIZATION,
            to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
            severity="critical",
            explanation="Synthetic non-compensable finding for precedence coverage.",
            operation_id="comp-op-001",
        )
    )

    assert is_fully_compensated(trace.events, findings) is False


def test_missing_operation_id_on_a_finding_blocks_compensation():
    trace = fixture("full_refund_compensated.json")
    findings = list(verify_causal_economic_outcome(trace.events))
    findings[0] = replace(findings[0], operation_id=None)

    assert is_fully_compensated(trace.events, findings) is False
