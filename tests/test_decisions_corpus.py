"""Checks of synthetic fixtures and projection, not Decisions model quality."""

from collections import Counter
from copy import deepcopy
import json

import pytest

from app.evidence import verify_receipt_integrity
from app.models import ExecutionReceipt
from scripts.decisions_corpus import EXPECTED_STATUS, SCENARIOS, build_corpus


@pytest.fixture(scope="module")
def corpus():
    return build_corpus()


def test_thirty_cases_have_stable_unique_ids_and_json_safe_payloads(corpus):
    assert len(corpus) == 30
    assert [case["case_id"] for case in corpus] == [f"APD-{i:03d}" for i in range(1, 31)]
    assert len({case["case_id"] for case in corpus}) == 30
    assert Counter(case["scenario"] for case in corpus) == dict.fromkeys(SCENARIOS, 3)
    assert Counter(case["variant"] for case in corpus) == {"01": 10, "02": 10, "03": 10}
    assert json.loads(json.dumps(corpus, allow_nan=False)) == corpus
    rebuilt = build_corpus()
    assert [(case["case_id"], case["scenario"], case["variant"]) for case in rebuilt] == [
        (case["case_id"], case["scenario"], case["variant"]) for case in corpus
    ]


def test_gold_labels_and_deterministic_status_balance_are_explicit(corpus):
    assert Counter(case["gold"]["needs_review"] for case in corpus) == {True: 27, False: 3}
    assert Counter(case["gold"]["primary_signal"] for case in corpus) == {
        "routine": 3,
        "authorization_gap": 9,
        "integrity_gap": 9,
        "replay": 3,
        "insufficient_evidence": 6,
    }
    assert Counter(case["gold"]["severity"] for case in corpus) == {0: 3, 1: 3, 2: 12, 3: 12}
    assert Counter(case["baseline"]["verdict"] for case in corpus) == {
        "VERIFIED": 6, "BLOCKED": 18, "DUPLICATE": 3, "UNVERIFIED": 3
    }
    for case in corpus:
        assert case["baseline"]["verdict"] == EXPECTED_STATUS[case["scenario"]].value
        assert type(case["gold"]["needs_review"]) is bool
        assert type(case["gold"]["severity"]) is int
        assert case["provenance"] == "synthetic-agentproof-mock-provider"


def test_baselines_remain_original_sealed_receipts(corpus):
    for case in corpus:
        baseline = case["baseline"]
        receipt = ExecutionReceipt.model_validate(baseline["receipt"])
        assert verify_receipt_integrity(receipt)
        assert receipt.status.value == baseline["verdict"]
        assert receipt.claimed_success is (baseline["verdict"] == "VERIFIED")
        settled = case["scenario"] in {"happy", "replay", "partial_trace"}
        assert baseline["ledger_count"] == int(settled)
        expected_money = case["model_evidence"]["invoice"]["amount_cents"] if settled else 0
        assert baseline["money_moved_cents"] == expected_money


def test_model_projection_excludes_gold_final_verdict_and_receipt_seal(corpus):
    forbidden = {"gold", "baseline", "verdict", "evidence_hash", "claimed_success"}

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    for case in corpus:
        evidence = case["model_evidence"]
        assert set(evidence) == {"invoice", "receipt_facts", "events"}
        assert forbidden.isdisjoint(keys(evidence))
        assert {"status", "case_id", "scenario", "primary_signal", "severity", "needs_review"}.isdisjoint(
            evidence["receipt_facts"]
        )
        # Invoice IDs identify a synthetic variant; they do not reveal a scenario label.
        assert evidence["invoice"]["invoice_id"] == f"SYN-INVOICE-{case['variant']}"


def test_partial_trace_changes_only_evidence_copy(corpus):
    for case in corpus:
        evidence = case["model_evidence"]
        baseline_events = case["baseline"]["receipt"]["events"]
        if case["scenario"] == "partial_trace":
            assert case["baseline"]["verdict"] == "VERIFIED"
            assert any(event["stage"] == "outcome" for event in baseline_events)
            assert not any(event["stage"] == "outcome" for event in evidence["events"])
            assert evidence["events"] == [event for event in baseline_events if event["stage"] != "outcome"]
        else:
            assert evidence["events"] == baseline_events


def test_mutating_evidence_cannot_mutate_baseline_receipt(corpus):
    case = deepcopy(next(case for case in corpus if case["scenario"] == "happy"))
    original_baseline = deepcopy(case["baseline"])
    case["model_evidence"]["receipt_facts"]["amount_cents"] = 1
    case["model_evidence"]["events"][0]["message"] = "tampered projection"
    case["gold"]["needs_review"] = True
    assert case["baseline"] == original_baseline
    assert verify_receipt_integrity(ExecutionReceipt.model_validate(case["baseline"]["receipt"]))
