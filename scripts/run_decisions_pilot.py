"""Prepare a synthetic advisory pilot; network access requires explicit --live."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.decisions_advisory import (  # noqa: E402
    MODEL, QUESTION_VERSION, build_request, canonical_json, evaluate_evidence,
)
from app.evidence import verify_receipt_integrity  # noqa: E402
from app.models import ExecutionReceipt  # noqa: E402
from scripts.decisions_corpus import SCENARIOS, build_corpus  # noqa: E402


SOURCE_FILES = (
    "app/decisions_advisory.py", "scripts/decisions_corpus.py",
    "scripts/run_decisions_pilot.py", "app/orchestrator.py", "app/policy.py",
    "app/payment_provider.py", "app/evidence.py", "app/state.py", "app/models.py",
    "app/planner.py",
)


def run_corpus(corpus, *, mode="prepare", api_key="", transport=None):
    if mode not in {"prepare", "mock", "live"}:
        raise ValueError("unknown pilot mode")
    if mode == "live" and transport is not None:
        raise ValueError("a substituted transport must use mock mode")
    if mode == "mock" and transport is None:
        raise ValueError("mock mode requires an explicit test transport")
    results = []
    statuses = Counter()
    tp = fp = tn = fn = review_answered = signal_answered = signal_matched = 0
    severity_errors = []
    refused_questions = 0
    valid_provider_responses = 0
    authority_unchanged = True
    for case in corpus:
        baseline_before = canonical_json(case["baseline"])
        baseline_valid = verify_receipt_integrity(ExecutionReceipt.model_validate(case["baseline"]["receipt"]))
        if not baseline_valid:
            raise ValueError("corpus contains an invalid baseline receipt")
        if mode == "prepare":
            advisory = {"status": "not_requested", "advisory_only": True, "answers": []}
        else:
            advisory = evaluate_evidence(case["model_evidence"], api_key=api_key, transport=transport)
        unchanged = baseline_before == canonical_json(case["baseline"])
        authority_unchanged = authority_unchanged and unchanged
        statuses[advisory["status"]] += 1
        if advisory["status"] in {"available", "partial", "refused"}:
            valid_provider_responses += 1
        refused_questions += sum(answer["type"] == "refusal" for answer in advisory["answers"])
        # Mocked outputs exercise plumbing only; never produce model-quality numbers.
        if mode == "live":
            for answer in advisory["answers"]:
                if answer["type"] == "predicate":
                    predicted = answer["probability"] >= 0.5
                    expected = case["gold"]["needs_review"]
                    review_answered += 1
                    tp += int(predicted and expected)
                    fp += int(predicted and not expected)
                    tn += int(not predicted and not expected)
                    fn += int(not predicted and expected)
                elif answer["type"] == "choice":
                    signal_answered += 1
                    signal_matched += int(answer["choice"] == case["gold"]["primary_signal"])
                elif answer["type"] == "score":
                    severity_errors.append(abs(answer["score"] - case["gold"]["severity"]))
        results.append({
            "case_id": case["case_id"], "scenario": case["scenario"],
            "baseline_verdict": case["baseline"]["verdict"],
            "baseline_integrity_valid": baseline_valid, "baseline_unchanged": unchanged,
            "advisory": advisory,
        })
    evaluated = mode == "live" and valid_provider_responses > 0
    return {
        "schema_version": "agentproof-decisions-pilot-v1", "mode": mode,
        "model": MODEL, "question_version": QUESTION_VERSION,
        "live_evaluated": evaluated,
        "model_quality_evaluated": mode == "live" and review_answered + signal_answered + len(severity_errors) > 0,
        "dataset_kind": "synthetic-agentproof-mock-provider",
        "total_cases": len(corpus), "scenario_types": len(SCENARIOS),
        "requests_attempted": len(corpus) if mode != "prepare" and api_key else 0,
        "valid_provider_responses": valid_provider_responses, "refused_questions": refused_questions,
        "statuses": dict(statuses), "authority_unchanged": authority_unchanged,
        "baseline_receipts_valid": len(corpus),
        "gold_balance": dict(Counter(case["gold"]["primary_signal"] for case in corpus)),
        "uncovered_signals": ["untrusted_instruction"],
        "metrics": {
            "review_threshold": 0.5 if evaluated else None,
            "threshold_status": "exploratory_uncalibrated",
            "review_answered": review_answered,
            "review_coverage": review_answered / len(corpus) if evaluated and corpus else None,
            "true_positives": tp if review_answered else None,
            "false_positives": fp if review_answered else None,
            "true_negatives": tn if review_answered else None,
            "false_negatives": fn if review_answered else None,
            "review_accuracy": (tp + tn) / review_answered if review_answered else None,
            "review_recall": tp / (tp + fn) if tp + fn else None,
            "signal_answered": signal_answered,
            "signal_accuracy": signal_matched / signal_answered if signal_answered else None,
            "severity_answered": len(severity_errors),
            "severity_mean_absolute_error": sum(severity_errors) / len(severity_errors) if severity_errors else None,
        },
        "limitations": [
            "Ten synthetic scenario types with three variants each; not 30 independent production failures.",
            "Gold labels are authored review rubric labels, not authorization decisions.",
            "Observed stage statuses are input evidence; the final verdict and gold labels are excluded.",
            "27 review cases and 3 routine controls; this is not a balanced quality benchmark.",
            "Receipts contain fresh UUIDs/timestamps; persisted corpus bytes are the replay artifact.",
            "Scoring cannot modify payment facts, permissions, dispatch, ledger or deterministic receipts.",
            "Refusals and errors are missing coverage, not routine/safe predictions.",
        ],
        "results": results,
    }


def _fingerprint(path):
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def save_bundle(directory: Path, corpus, report):
    directory.mkdir(parents=True, exist_ok=False)
    _write_bundle(directory, corpus, report)


def _write_bundle(directory: Path, corpus, report):
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    report["source_files"] = {name: _fingerprint(ROOT / name) for name in SOURCE_FILES}
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    report["source_git_head"] = git.stdout.strip() if git.returncode == 0 else None
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    report["source_worktree_dirty"] = bool(dirty.stdout.strip()) if dirty.returncode == 0 else None
    corpus_text = "".join(canonical_json(case) + "\n" for case in corpus)
    requests_text = "".join(canonical_json({
        "case_id": case["case_id"], "request": build_request(case["model_evidence"]),
    }) + "\n" for case in corpus)
    for name, content in (
        ("corpus.jsonl", corpus_text), ("requests.jsonl", requests_text),
        ("report.json", json.dumps(report, indent=2, allow_nan=False) + "\n"),
    ):
        with (directory / name).open("x", encoding="utf-8") as file:
            file.write(content)
    manifest = {
        "schema_version": "agentproof-decisions-bundle-v1",
        "files": {name: _fingerprint(directory / name) for name in ("corpus.jsonl", "requests.jsonl", "report.json")},
    }
    with (directory / "manifest.json").open("x", encoding="utf-8") as file:
        file.write(json.dumps(manifest, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send the synthetic corpus to the paid Decisions API.")
    parser.add_argument("--output", type=Path, help="New evidence directory; existing directories are never overwritten.")
    args = parser.parse_args(argv)
    output = args.output or ROOT / "artifacts" / "decisions-pilot" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    # Reserve an exclusive output directory before credentials or paid requests.
    output.mkdir(parents=True, exist_ok=False)
    # Offline preparation does not even look up credentials.
    api_key = os.environ.get("OPENAI_API_KEY", "") if args.live else ""
    corpus = build_corpus()
    report = run_corpus(corpus, mode="live" if args.live else "prepare", api_key=api_key)
    _write_bundle(output, corpus, report)
    print(json.dumps({
        "mode": report["mode"], "total_cases": report["total_cases"],
        "live_evaluated": report["live_evaluated"], "statuses": report["statuses"],
        "authority_unchanged": report["authority_unchanged"], "output": str(output.resolve()),
    }))
    if args.live and report["statuses"].get("unavailable", 0) + report["statuses"].get("malformed", 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
