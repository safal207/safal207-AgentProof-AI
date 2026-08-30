from pathlib import Path

from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import build_trace_report, load_trace


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "astra_fulfillment"
EXPECTED_FIXTURES = {
    "issued_client_response_lost.json",
    "issued_delivered_reconciled.json",
    "refund_binding_unresolved.json",
    "settled_issuer_failed.json",
}


def event(stage, key, value, source, **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def findings(events):
    return verify_causal_economic_outcome(events)


def codes(events):
    return {finding.code for finding in findings(events)}


def settled(operation_id="operation-1", payment_id="payment-1"):
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "payment_status",
        "settled",
        "independent-ledger",
        authoritative=True,
        operation_id=operation_id,
        payment_id=payment_id,
    )


def test_authoritative_issuer_failure_after_settlement_is_detected():
    found = codes(
        [
            settled(),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "issuer_status",
                "failed",
                "issuer-terminal-record",
                authoritative=True,
                operation_id="operation-1",
            ),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "delivery_status",
                "failed",
                "merchant-delivery-record",
                operation_id="operation-1",
            ),
        ]
    )

    assert "SETTLED_FULFILLMENT_FAILED" in found
    assert "SETTLED_BUT_NOT_DELIVERED" in found


def test_non_authoritative_failure_does_not_become_provider_truth():
    found = codes(
        [
            settled(),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "issuer_status",
                "failed",
                "untrusted-callback",
                authoritative=False,
                operation_id="operation-1",
            ),
        ]
    )

    assert "SETTLED_FULFILLMENT_FAILED" not in found


def test_later_untrusted_callback_cannot_mask_authoritative_failure():
    found = codes(
        [
            settled(),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "issuer_status",
                "failed",
                "issuer-terminal-record",
                authoritative=True,
                operation_id="operation-1",
            ),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "issuer_status",
                "issued",
                "untrusted-callback",
                authoritative=False,
                operation_id="operation-1",
            ),
        ]
    )

    assert "SETTLED_FULFILLMENT_FAILED" in found
    assert "ISSUED_BUT_CLIENT_UNOBSERVED" not in found


def test_repeated_settlement_updates_emit_one_specialized_finding():
    result = findings(
        [
            settled(),
            settled(),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "issuer_status",
                "failed",
                "issuer-terminal-record",
                authoritative=True,
                operation_id="operation-1",
            ),
        ]
    )

    assert sum(
        item.code == "SETTLED_FULFILLMENT_FAILED"
        for item in result
    ) == 1


def test_issued_but_explicitly_unobserved_by_client_is_detected():
    found = codes(
        [
            settled(),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "issuer_status",
                "issued",
                "issuer-terminal-record",
                authoritative=True,
                operation_id="operation-1",
            ),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "client_delivery_status",
                "response_lost",
                "client-transport-record",
                operation_id="operation-1",
            ),
        ]
    )

    assert "ISSUED_BUT_CLIENT_UNOBSERVED" in found


def test_missing_delivery_event_alone_does_not_prove_client_unobserved():
    found = codes(
        [
            settled(),
            event(
                Stage.RESOURCE_OUTCOME_DELIVERY,
                "issuer_status",
                "issued",
                "issuer-terminal-record",
                authoritative=True,
                operation_id="operation-1",
            ),
        ]
    )

    assert "ISSUED_BUT_CLIENT_UNOBSERVED" not in found


def refund_movement():
    return event(
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        "candidate_refund_status",
        "refunded",
        "independent-ledger",
        authoritative=True,
        payment_id="refund-1",
    )


def refund_binding(
    *,
    status="unresolved",
    authoritative=False,
    source="provider-reconciliation-record",
):
    return event(
        Stage.RECONCILIATION,
        "refund_operation_binding",
        {
            "status": status,
            "payment_id": "refund-1",
        },
        source,
        authoritative=authoritative,
        operation_id="operation-1",
    )


def refund_events(*, binding_status="unresolved", binding_authoritative=False):
    return [
        refund_movement(),
        refund_binding(
            status=binding_status,
            authoritative=binding_authoritative,
        ),
    ]


def test_unbound_refund_is_not_promoted_to_terminal_recovery():
    assert "REFUND_OPERATION_BINDING_UNRESOLVED" in codes(refund_events())


def test_non_authoritative_bound_marker_does_not_close_refund_gap():
    assert "REFUND_OPERATION_BINDING_UNRESOLVED" in codes(
        refund_events(binding_status="bound")
    )


def test_authoritative_bound_refund_closes_attribution_gap():
    assert "REFUND_OPERATION_BINDING_UNRESOLVED" not in codes(
        refund_events(binding_status="bound", binding_authoritative=True)
    )


def test_authoritative_bound_with_unresolved_confidence_stays_open():
    contradictory = event(
        Stage.RECONCILIATION,
        "refund_operation_binding",
        {
            "status": "bound",
            "confidence": "unresolved",
            "payment_id": "refund-1",
        },
        "provider-reconciliation-record",
        authoritative=True,
        operation_id="operation-1",
    )

    found = codes([refund_movement(), contradictory])
    assert "REFUND_OPERATION_BINDING_UNRESOLVED" in found


def test_later_authoritative_bound_record_supersedes_unresolved_history():
    found = codes(
        [
            refund_movement(),
            refund_binding(status="unresolved", authoritative=False),
            refund_binding(status="bound", authoritative=True),
        ]
    )

    assert "REFUND_OPERATION_BINDING_UNRESOLVED" not in found


def test_later_untrusted_bound_marker_cannot_override_authoritative_unresolved_state():
    found = codes(
        [
            refund_movement(),
            refund_binding(status="unresolved", authoritative=True),
            refund_binding(
                status="bound",
                authoritative=False,
                source="untrusted-correlation-layer",
            ),
        ]
    )

    assert "REFUND_OPERATION_BINDING_UNRESOLVED" in found


def test_malformed_authoritative_binding_cannot_hide_unresolved_state():
    malformed = event(
        Stage.RECONCILIATION,
        "refund_operation_binding",
        {
            "status": "provider-specific-garbage",
            "payment_id": "refund-1",
        },
        "malformed-provider-record",
        authoritative=True,
        operation_id="operation-1",
    )

    found = codes(
        [
            refund_movement(),
            refund_binding(status="unresolved", authoritative=False),
            malformed,
        ]
    )

    assert "REFUND_OPERATION_BINDING_UNRESOLVED" in found


def test_all_provider_fulfillment_fixtures_match_oracles():
    paths = sorted(FIXTURES.glob("*.json"))
    assert {path.name for path in paths} == EXPECTED_FIXTURES

    for path in paths:
        trace = load_trace(path)
        report = build_trace_report(trace)
        assert {finding.code for finding in report.findings} == set(trace.expected_codes)
        assert report.verdict == trace.expected_verdict
        assert len(report.evidence_hash) == 64
