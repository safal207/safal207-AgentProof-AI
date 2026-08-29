from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.astra_trace import build_trace_report, load_trace, report_to_mapping


def _fixture_paths(values: list[str]) -> list[Path]:
    if values:
        paths: list[Path] = []
        for value in values:
            path = Path(value)
            if path.is_dir():
                paths.extend(sorted(path.glob("*.json")))
            else:
                paths.append(path)
        return paths
    return sorted((ROOT / "fixtures" / "astra").glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run reproducible Astra causal/economic verification fixtures."
    )
    parser.add_argument("paths", nargs="*", help="Fixture files or directories.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON report per line.",
    )
    args = parser.parse_args()

    failed = False
    paths = _fixture_paths(args.paths)
    if not paths:
        parser.error("no fixture files found")

    for path in paths:
        trace = load_trace(path)
        report = build_trace_report(trace)
        actual = {finding.code for finding in report.findings}
        expected = set(trace.expected_codes)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        verdict_mismatch = bool(
            trace.expected_verdict is not None
            and report.verdict != trace.expected_verdict
        )
        expectation_match = not missing and not unexpected and not verdict_mismatch
        if not expectation_match:
            failed = True

        if args.json:
            output = report_to_mapping(report)
            output["fixture"] = str(path)
            output["expected_verdict"] = trace.expected_verdict
            output["expectation_match"] = expectation_match
            output["missing_expected_codes"] = missing
            output["unexpected_codes"] = unexpected
            output["verdict_mismatch"] = verdict_mismatch
            print(json.dumps(output, sort_keys=True))
        else:
            codes = ", ".join(sorted(actual)) or "none"
            status = "PASS" if expectation_match else "FAIL"
            print(
                f"[{status}] {trace.trace_id} | {report.verdict} | "
                f"{codes} | sha256:{report.evidence_hash}"
            )
            if missing:
                print(f"  missing expected: {', '.join(missing)}")
            if unexpected:
                print(f"  unexpected: {', '.join(unexpected)}")
            if verdict_mismatch:
                print(
                    f"  expected verdict: {trace.expected_verdict}; "
                    f"actual: {report.verdict}"
                )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
