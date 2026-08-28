from decimal import Decimal

import pytest

from app.astra_spider import Stage, StateEvent, verify_causal_economic_outcome
from app.astra_trace import HASH_PROFILE, build_trace_report, trace_from_mapping


def event(stage, key, value, source="test", **kwargs):
    return StateEvent(stage=stage, key=key, value=value, source=source, **kwargs)


def codes(events):
    return {finding.code for finding in verify_causal_economic_outcome(events)}


def test_atomic_amounts_above_float_precision_remain_exact():
    found = codes(
        [
            event(
                Stage.MANDATE_AUTHORIZATION,
                "authorized_amount_minor",
                9_007_199_254_740_992,
                operation_id="op-large",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "captured_amount_minor",
                9_007_199_254_740_993,
                authoritative=True,
                operation_id="op-large",
            ),
        ]
    )
    assert "OVER_CAPTURE" in found


def test_synonymous_amount_views_are_not_double_counted():
    found = codes(
        [
            event(
                Stage.MANDATE_AUTHORIZATION,
                "authorized_amount_minor",
                100,
                operation_id="op-amount",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "settled_amount_minor",
                100,
                authoritative=True,
                operation_id="op-amount",
                payment_id="pay-1",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "captured_amount_minor",
                100,
                authoritative=True,
                operation_id="op-amount",
                payment_id="pay-1",
            ),
        ]
    )
    assert "OVER_CAPTURE" not in found


def test_partial_captures_sum_exactly_with_decimal_strings():
    found = codes(
        [
            event(
                Stage.MANDATE_AUTHORIZATION,
                "authorized_amount_minor",
                "0.30",
                operation_id="op-decimal",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "captured_amount_minor",
                "0.10",
                authoritative=True,
                operation_id="op-decimal",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "captured_amount_minor",
                "0.20",
                authoritative=True,
                operation_id="op-decimal",
            ),
        ]
    )
    assert "OVER_CAPTURE" not in found


def test_explicit_multi_settlement_bound_prevents_false_duplicate():
    found = codes(
        [
            event(Stage.QUOTE_CHALLENGE, "payment_flow", "escrow", operation_id="op-escrow"),
            event(
                Stage.MANDATE_AUTHORIZATION,
                "expected_settlement_count",
                2,
                operation_id="op-escrow",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                authoritative=True,
                operation_id="op-escrow",
                payment_id="hold-1",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                authoritative=True,
                operation_id="op-escrow",
                payment_id="capture-1",
            ),
        ]
    )
    assert "RETRY_DUPLICATE_PAYMENT" not in found
    assert "MULTI_SETTLEMENT_UNRESOLVED" not in found


def test_multi_settlement_mode_without_expected_count_is_unresolved():
    found = codes(
        [
            event(Stage.QUOTE_CHALLENGE, "payment_flow", "batch", operation_id="op-batch"),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                authoritative=True,
                operation_id="op-batch",
                payment_id="pay-1",
            ),
            event(
                Stage.ACTUAL_SETTLEMENT_FINALITY,
                "payment_status",
                "settled",
                authoritative=True,
                operation_id="op-batch",
                payment_id="pay-2",
            ),
        ]
    )
    assert "RETRY_DUPLICATE_PAYMENT" not in found
    assert "MULTI_SETTLEMENT_UNRESOLVED" in found


def test_trace_parser_rejects_string_boolean_authority():
    with pytest.raises(ValueError, match="authoritative must be a JSON boolean"):
        trace_from_mapping(
            {
                "trace_id": "trace-1",
                "protocol": "test",
                "scenario": "invalid authority type",
                "events": [
                    {
                        "stage": "ACTUAL SETTLEMENT/FINALITY",
                        "key": "payment_status",
                        "value": "settled",
                        "source": "psp",
                        "authoritative": "false",
                    }
                ],
            }
        )


def test_hash_profile_is_versioned_and_decimal_evidence_is_supported():
    trace = trace_from_mapping(
        {
            "trace_id": "trace-2",
            "protocol": "test",
            "scenario": "hash profile",
            "events": [
                {
                    "stage": "MANDATE/AUTHORIZATION",
                    "key": "authorized_amount_minor",
                    "value": 100,
                    "source": "mandate",
                }
            ],
        }
    )
    event_with_decimal = StateEvent(
        stage=trace.events[0].stage,
        key=trace.events[0].key,
        value=Decimal("100.00"),
        source=trace.events[0].source,
    )
    report = build_trace_report(
        trace.__class__(
            trace_id=trace.trace_id,
            protocol=trace.protocol,
            scenario=trace.scenario,
            events=(event_with_decimal,),
        )
    )
    assert report.hash_profile == HASH_PROFILE
    assert len(report.evidence_hash) == 64
