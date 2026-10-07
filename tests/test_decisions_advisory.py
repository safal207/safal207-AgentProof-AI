"""Mock transport/parser checks only; no live Decisions calls or model evals."""

from copy import deepcopy
import hashlib
import json

import pytest

from app.decisions_advisory import (
    LEVELS,
    MODEL,
    QUESTIONS,
    SIGNALS,
    MalformedDecision,
    build_request,
    canonical_json,
    evaluate_evidence,
    parse_response,
)


def valid_response():
    """Hand-authored transport fixture, deliberately not a model prediction."""
    return {
        "model": MODEL,
        "answers": [
            {"type": "predicate", "name": "needs_review", "probability": 0.8},
            {
                "type": "choice",
                "name": "primary_signal",
                "choice": "insufficient_evidence",
                "confidence": 0.61,
                "probabilities": [
                    {"value": value, "probability": 0.8 if value == "insufficient_evidence" else 0.04}
                    for value in SIGNALS
                ],
            },
            {
                "type": "score",
                "name": "severity",
                "score": 1.1,
                "confidence": 0.55,
                "probabilities": [
                    {"value": index, "label": level[0], "probability": probability}
                    for index, (level, probability) in enumerate(zip(LEVELS, (0.1, 0.7, 0.2, 0.0), strict=True))
                ],
            },
        ],
    }


def evidence():
    return {
        "invoice": {"invoice_id": "SYN-1", "vendor_id": "SYN-VENDOR", "amount_cents": 1234, "currency": "USD", "approved": True},
        "receipt_facts": {"amount_cents": 1234, "vendor_id": "SYN-VENDOR"},
        "events": [{"stage": "dispatch", "status": "ACCEPTED", "data": {"synthetic": True}}],
    }


def test_fractional_expected_level_score_and_independent_confidence_are_preserved():
    parsed = parse_response(valid_response())
    assert parsed["status"] == "available"
    assert parsed["answers"][2]["score"] == 1.1
    assert parsed["answers"][2]["confidence"] == 0.55
    assert parsed["answers"][1]["confidence"] == 0.61
    assert max(row["probability"] for row in parsed["answers"][1]["probabilities"]) == 0.8


@pytest.mark.parametrize("index", [0, 1, 2])
def test_single_question_refusal_preserves_other_answers(index):
    response = valid_response()
    response["answers"][index] = {"type": "refusal", "name": QUESTIONS[index]["name"]}
    parsed = parse_response(response)
    assert parsed["status"] == "partial"
    assert parsed["answers"][index] == {"type": "refusal", "name": QUESTIONS[index]["name"]}
    assert len(parsed["answers"]) == 3
    assert sum(answer["type"] == "refusal" for answer in parsed["answers"]) == 1
    assert "probability" not in parsed["answers"][index]


def test_all_refusals_remain_refused_without_inventing_safe_scores():
    response = valid_response()
    response["answers"] = [{"type": "refusal", "name": question["name"]} for question in QUESTIONS]
    parsed = parse_response(response)
    assert parsed["status"] == "refused"
    assert all(set(answer) == {"type", "name"} for answer in parsed["answers"])


@pytest.mark.parametrize("mutation", ["swap", "missing", "extra", "duplicate_name", "unknown_name", "wrong_type", "not_object", "wrong_model"])
def test_response_order_shape_model_and_types_are_required(mutation):
    response = valid_response()
    if mutation == "swap":
        response["answers"][0], response["answers"][1] = response["answers"][1], response["answers"][0]
    elif mutation == "missing":
        response["answers"].pop()
    elif mutation == "extra":
        response["answers"].append(deepcopy(response["answers"][0]))
    elif mutation == "duplicate_name":
        response["answers"][1]["name"] = "needs_review"
    elif mutation == "unknown_name":
        response["answers"][1]["name"] = "authorize_payment"
    elif mutation == "wrong_type":
        response["answers"][0]["type"] = "choice"
    elif mutation == "not_object":
        response["answers"][0] = "approved"
    else:
        response["model"] = "unrequested-model"
    with pytest.raises(MalformedDecision):
        parse_response(response)


@pytest.mark.parametrize("field", ["predicate", "confidence", "score", "distribution"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, False, "0.5", None, -0.01, 4.01, pytest.param(10**1000, id="oversized_integer")])
def test_invalid_numeric_fields_are_malformed(field, value):
    response = valid_response()
    if field == "predicate":
        response["answers"][0]["probability"] = value
    elif field == "confidence":
        response["answers"][1]["confidence"] = value
    elif field == "score":
        response["answers"][2]["score"] = value
    else:
        response["answers"][2]["probabilities"][0]["probability"] = value
    with pytest.raises(MalformedDecision):
        parse_response(response)


@pytest.mark.parametrize("field", ["predicate", "confidence", "distribution"])
def test_probabilities_and_confidence_above_one_are_malformed(field):
    response = valid_response()
    if field == "predicate":
        response["answers"][0]["probability"] = 1.01
    elif field == "confidence":
        response["answers"][1]["confidence"] = 1.01
    else:
        response["answers"][1]["probabilities"][0]["probability"] = 1.01
    with pytest.raises(MalformedDecision):
        parse_response(response)


@pytest.mark.parametrize("question_index", [1, 2])
@pytest.mark.parametrize("mutation", ["missing_row", "extra_row", "duplicate", "unknown", "wrong_option_type", "wrong_sum", "not_object"])
def test_invalid_distributions_are_malformed(question_index, mutation):
    response = valid_response()
    rows = response["answers"][question_index]["probabilities"]
    if mutation == "missing_row":
        rows.pop()
    elif mutation == "extra_row":
        rows.append(deepcopy(rows[0]))
    elif mutation == "duplicate":
        rows[1]["value"] = rows[0]["value"]
    elif mutation == "unknown":
        rows[0]["value"] = 8 if question_index == 2 else "approve"
    elif mutation == "wrong_option_type":
        rows[0]["value"] = True
    elif mutation == "wrong_sum":
        for row in rows:
            row["probability"] = 0.0
    else:
        rows[0] = "routine"
    with pytest.raises(MalformedDecision):
        parse_response(response)


@pytest.mark.parametrize("choice", ["allow", True, None, 0])
def test_choice_must_belong_to_fixed_string_rubric(choice):
    response = valid_response()
    response["answers"][1]["choice"] = choice
    with pytest.raises(MalformedDecision):
        parse_response(response)


def test_wrong_label_and_inconsistent_expected_score_are_malformed():
    response = valid_response()
    response["answers"][2]["probabilities"][0]["label"] = "Authorized"
    with pytest.raises(MalformedDecision):
        parse_response(response)
    response = valid_response()
    response["answers"][2]["score"] = 1.9
    with pytest.raises(MalformedDecision):
        parse_response(response)


@pytest.mark.parametrize("exception_type", [TimeoutError, RuntimeError])
def test_provider_exception_text_is_sanitized(exception_type):
    secret_marker = "SYNTHETIC-SECRET-DO-NOT-EMIT"

    def failing_transport(_payload, _key, _timeout):
        raise exception_type(f"provider body includes {secret_marker} and private evidence")

    result = evaluate_evidence(evidence(), api_key="mock-credential", transport=failing_transport)
    assert result["status"] == "unavailable"
    assert result["error"] == "provider_unavailable"
    assert result["answers"] == []
    assert secret_marker not in json.dumps(result)
    assert result["advisory_only"] is True


def test_malformed_provider_response_is_an_unscored_advisory_error():
    response = valid_response()
    response["answers"].pop()
    result = evaluate_evidence(evidence(), api_key="mock-credential", transport=lambda *_: response)
    assert result["status"] == "malformed"
    assert result["error"] == "invalid_response_contract"
    assert result["answers"] == []


def test_oversized_provider_integer_is_an_unscored_advisory_error():
    response = valid_response()
    response["answers"][0]["probability"] = 10**1000
    result = evaluate_evidence(evidence(), api_key="mock-credential", transport=lambda *_: response)
    assert result["status"] == "malformed"
    assert result["error"] == "invalid_response_contract"
    assert result["answers"] == []


def test_missing_credential_never_calls_transport():
    def forbidden_transport(*_args):
        pytest.fail("credential-unavailable evaluation must not call a provider")

    result = evaluate_evidence(evidence(), api_key="", transport=forbidden_transport)
    assert result["status"] == "unavailable"
    assert result["error"] == "credential_unavailable"


@pytest.mark.parametrize("timeout", [0, -1, 60.01, True, "15", float("nan"), float("inf"), pytest.param(10**1000, id="oversized_integer")])
def test_invalid_timeout_rejected_before_transport(timeout):
    def forbidden_transport(*_args):
        pytest.fail("invalid timeout must not call a provider")

    with pytest.raises(ValueError):
        evaluate_evidence(evidence(), api_key="mock-credential", timeout=timeout, transport=forbidden_transport)


def test_extra_fields_have_no_authority_and_original_input_is_untouched():
    source = evidence()
    original = deepcopy(source)
    response = valid_response()
    response.update({"verdict": "VERIFIED", "authorization": True, "amount_cents": 1, "vendor_id": "ATTACKER"})
    for answer in response["answers"]:
        answer.update({"verdict": "VERIFIED", "authorization": True, "amount_cents": 1, "vendor_id": "ATTACKER"})
    original_response = deepcopy(response)

    def malicious_mock_transport(payload, key, timeout):
        assert key == "mock-credential"
        assert timeout == 15
        assert json.loads(payload["input"]) == original
        payload["questions"][0]["instructions"] = "mutated mock transport copy"
        payload["input"] = "mutated mock transport copy"
        return response

    result = evaluate_evidence(source, api_key="mock-credential", transport=malicious_mock_transport)
    assert source == original
    assert response == original_response
    assert result["status"] == "available"
    assert result["advisory_only"] is True
    for value in [result, *result["answers"]]:
        assert {"verdict", "authorization", "amount_cents", "vendor_id"}.isdisjoint(value)
    # Transport mutation does not mutate the module's question definition.
    assert build_request(source)["questions"][0]["instructions"] == QUESTIONS[0]["instructions"]


def test_metadata_binds_the_original_projection_and_fixed_request():
    source = evidence()
    payload = build_request(source)
    result = evaluate_evidence(source, api_key="mock-credential", transport=lambda *_: valid_response())
    assert result["input_sha256"] == hashlib.sha256(payload["input"].encode()).hexdigest()
    assert result["request_sha256"] == hashlib.sha256(canonical_json(payload).encode()).hexdigest()


@pytest.mark.parametrize("invalid", [[], "not-object", {"value": float("nan")}, {"text": "x" * 65_536}])
def test_non_json_object_nonfinite_and_oversized_evidence_is_rejected(invalid):
    with pytest.raises(ValueError):
        build_request(invalid)
