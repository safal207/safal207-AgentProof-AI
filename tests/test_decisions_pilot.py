"""Pilot bookkeeping and offline boundaries; no live model evaluation."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.decisions_advisory import MODEL
from scripts.decisions_corpus import build_corpus
from scripts import run_decisions_pilot as pilot


@pytest.fixture(scope="module")
def corpus():
    return build_corpus()


def test_preparation_does_not_call_a_provider_or_claim_quality(corpus, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("preparation must not request model scores")

    monkeypatch.setattr(pilot, "evaluate_evidence", forbidden)
    original = deepcopy(corpus)
    report = pilot.run_corpus(corpus)
    assert corpus == original
    assert report["requests_attempted"] == report["valid_provider_responses"] == 0
    assert report["live_evaluated"] is report["model_quality_evaluated"] is False
    assert report["metrics"]["review_accuracy"] is None
    assert report["metrics"]["false_negatives"] is None
    assert report["statuses"] == {"not_requested": 30}
    assert report["authority_unchanged"] is True


def test_default_cli_never_looks_up_api_credentials(corpus, monkeypatch, tmp_path):
    class DeniedEnvironment:
        def get(self, name, default=""):
            raise AssertionError("offline CLI must not look up credentials")

    monkeypatch.setattr(pilot, "os", SimpleNamespace(environ=DeniedEnvironment()))
    monkeypatch.setattr(pilot, "build_corpus", lambda: corpus)
    output = tmp_path / "prepared"
    assert pilot.main(["--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["mode"] == "prepare"
    assert report["model_quality_evaluated"] is False


def test_live_without_key_preserves_failure_bundle_and_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(pilot, "os", SimpleNamespace(environ={}))
    output = tmp_path / "unavailable"
    assert pilot.main(["--live", "--output", str(output)]) == 2
    report = json.loads((output / "report.json").read_text())
    assert report["statuses"] == {"unavailable": 30}
    assert report["requests_attempted"] == 0
    assert report["live_evaluated"] is report["model_quality_evaluated"] is False
    assert report["metrics"]["review_accuracy"] is None
    assert all(row["advisory"]["error"] == "credential_unavailable" for row in report["results"])


def test_mocked_provider_results_never_report_model_accuracy(corpus):
    def refusal_transport(payload, key, timeout):
        return {"model": MODEL, "answers": [
            {"name": question["name"], "type": "refusal"} for question in payload["questions"]
        ]}

    report = pilot.run_corpus(corpus, mode="mock", api_key="synthetic-test-key", transport=refusal_transport)
    assert report["valid_provider_responses"] == 30
    assert report["refused_questions"] == 90
    assert report["live_evaluated"] is report["model_quality_evaluated"] is False
    assert report["metrics"]["review_accuracy"] is None
    assert report["metrics"]["review_coverage"] is None


def test_custom_transport_cannot_be_labeled_live(corpus):
    with pytest.raises(ValueError, match="mock mode"):
        pilot.run_corpus(corpus, mode="live", transport=lambda *_: {})


def test_metric_denominators_exclude_errors_and_refusals(corpus, monkeypatch):
    # Simulate parsed responses to test arithmetic, not model quality.
    rows = [deepcopy(corpus[0]), deepcopy(corpus[3])]
    outputs = iter([
        {"status": "partial", "answers": [
            {"name": "needs_review", "type": "predicate", "probability": 0.2},
            {"name": "primary_signal", "type": "refusal"},
            {"name": "severity", "type": "score", "score": 1.1},
        ]},
        {"status": "malformed", "answers": []},
    ])
    monkeypatch.setattr(pilot, "evaluate_evidence", lambda *args, **kwargs: next(outputs))
    report = pilot.run_corpus(rows, mode="live", api_key="synthetic-test-key")
    metrics = report["metrics"]
    assert metrics["review_answered"] == 1
    assert metrics["review_coverage"] == 0.5
    assert metrics["true_negatives"] == 1
    assert metrics["false_negatives"] == 0
    assert metrics["signal_answered"] == 0
    assert metrics["signal_accuracy"] is None
    assert metrics["severity_mean_absolute_error"] == pytest.approx(1.1)
    assert report["refused_questions"] == 1


def test_manifest_binds_bundle_bytes_and_reports_source_hashes(corpus, tmp_path):
    output = tmp_path / "bundle"
    pilot.save_bundle(output, corpus, pilot.run_corpus(corpus))
    manifest = json.loads((output / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        data = (output / name).read_bytes()
        assert len(data) == expected["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == expected["sha256"]
    requests = [json.loads(line) for line in (output / "requests.jsonl").read_text().splitlines()]
    assert len(requests) == 30
    assert all(set(json.loads(row["request"]["input"])) == {"invoice", "receipt_facts", "events"} for row in requests)
    report = json.loads((output / "report.json").read_text())
    for name, expected in report["source_files"].items():
        assert hashlib.sha256((pilot.ROOT / name).read_bytes()).hexdigest() == expected["sha256"]
    (output / "corpus.jsonl").write_text("tampered")
    assert hashlib.sha256((output / "corpus.jsonl").read_bytes()).hexdigest() != manifest["files"]["corpus.jsonl"]["sha256"]


def test_bundle_never_overwrites_an_existing_attempt(corpus, tmp_path):
    output = tmp_path / "bundle"
    pilot.save_bundle(output, corpus, pilot.run_corpus(corpus))
    original = (output / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        pilot.save_bundle(output, corpus, pilot.run_corpus(corpus))
    assert (output / "report.json").read_bytes() == original


def test_existing_output_fails_before_credentials_or_paid_requests(monkeypatch, tmp_path):
    class DeniedEnvironment:
        def get(self, name, default=""):
            raise AssertionError("credential lookup must follow output reservation")

    def forbidden(*args, **kwargs):
        raise AssertionError("existing output must fail before any scoring")

    monkeypatch.setattr(pilot, "os", SimpleNamespace(environ=DeniedEnvironment()))
    monkeypatch.setattr(pilot, "run_corpus", forbidden)
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        pilot.main(["--live", "--output", str(output)])


def test_malformed_response_is_not_counted_as_a_valid_provider_response(corpus):
    report = pilot.run_corpus(corpus[:1], mode="mock", api_key="synthetic-test-key", transport=lambda *_: {
        "model": MODEL, "answers": [],
    })
    assert report["requests_attempted"] == 1
    assert report["valid_provider_responses"] == 0
    assert report["statuses"] == {"malformed": 1}
