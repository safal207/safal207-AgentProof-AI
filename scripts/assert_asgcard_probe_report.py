from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


def validate_report(report: Mapping[str, Any]) -> None:
    settlement = report.get("settlement")
    if not isinstance(settlement, Mapping):
        raise ValueError("settlement must be an object")

    operations = settlement.get("operations")
    if not isinstance(operations, list):
        raise ValueError("settlement.operations must be an array")
    operation_types = {
        str(operation.get("type"))
        for operation in operations
        if isinstance(operation, Mapping) and operation.get("type") is not None
    }
    if "invoke_host_function" not in operation_types:
        raise ValueError(
            "settlement transaction does not contain invoke_host_function"
        )

    ledger_view = report.get("ledger_view_divergence")
    if not isinstance(ledger_view, Mapping):
        raise ValueError("ledger_view_divergence must be an object")
    if ledger_view.get("account_payments_candidate_outgoing_usdc_count") != 0:
        raise ValueError(
            "classic account payment view unexpectedly exposes an outgoing USDC payment"
        )

    matching_types = ledger_view.get("account_payments_matching_record_types")
    if not isinstance(matching_types, list) or "invoke_host_function" not in matching_types:
        raise ValueError(
            "account payment view no longer identifies the matching record as invoke_host_function"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the ASG Card read-only Horizon probe report."
    )
    parser.add_argument(
        "report",
        nargs="?",
        default="artifacts/asgcard_issue17_ledger_probe.json",
    )
    args = parser.parse_args()

    path = Path(args.report)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("probe report must be a JSON object")
    validate_report(payload)
    print("ASG Card probe report assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
