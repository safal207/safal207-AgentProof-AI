from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

HORIZON = "https://horizon.stellar.org"
ACCOUNT = "GDKCNIN2WTC6UAARM4Z2IEWVRQBKKTN5XYFAMR5LFE7I2ZXF5L22CVX5"
INCIDENT_STARTED_AT = "2026-08-30T01:15:00Z"
SETTLEMENT_TX = "2b02ade834ffaa839cdfeca4409af049121a014abe0cf5bab95801012d2fe134"
POST_INCIDENT_CREDIT_TX = "df06e0ed59caf8c4fa388c71c054c75dbd8aa65750bbf9cddb5f4a149d5707e8"
USDC_ISSUER = "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN"
SETTLEMENT_DESTINATION = "GAHYHA55RTD2J4LAVJILTNHWMF2H2YVK5QXLQT3CHCJSVET3VRWPOCW6"
_CLASSIC_PAYMENT_TYPES = {
    "payment",
    "path_payment_strict_receive",
    "path_payment_strict_send",
}


def _get_json(path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{HORIZON}{path}",
        headers={
            "Accept": "application/hal+json, application/json",
            "User-Agent": "Astra-Spider-read-only-probe/1.2",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"Horizon returned a non-object for {path}")
    return payload


def _records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    embedded = payload.get("_embedded")
    if not isinstance(embedded, Mapping):
        return []
    records = embedded.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _effect_view(effect: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "created_at",
        "type",
        "type_i",
        "account",
        "amount",
        "asset_type",
        "asset_code",
        "asset_issuer",
    )
    return {key: effect[key] for key in keys if key in effect}


def _operation_view(operation: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "created_at",
        "type",
        "type_i",
        "transaction_hash",
        "source_account",
        "from",
        "to",
        "amount",
        "asset_type",
        "asset_code",
        "asset_issuer",
    )
    return {key: operation[key] for key in keys if key in operation}


def _transaction_view(transaction: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "hash",
        "created_at",
        "successful",
        "source_account",
        "fee_account",
        "fee_charged",
        "operation_count",
    )
    return {key: transaction[key] for key in keys if key in transaction}


def _transaction_bundle(tx_hash: str) -> dict[str, Any]:
    transaction = _get_json(f"/transactions/{tx_hash}")
    operations = _records(_get_json(f"/transactions/{tx_hash}/operations?order=asc&limit=20"))
    effects = _records(_get_json(f"/transactions/{tx_hash}/effects?order=asc&limit=50"))
    return {
        "transaction": _transaction_view(transaction),
        "operations": [_operation_view(operation) for operation in operations],
        "effects": [_effect_view(effect) for effect in effects],
    }


def _usdc_effects(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    effects = bundle.get("effects")
    if not isinstance(effects, list):
        return []
    return [
        effect
        for effect in effects
        if isinstance(effect, dict)
        and effect.get("asset_code") == "USDC"
        and effect.get("asset_issuer") == USDC_ISSUER
    ]


def _account_payment_view() -> dict[str, Any]:
    payload = _get_json(f"/accounts/{ACCOUNT}/payments?order=desc&limit=200")
    matching = [
        record
        for record in _records(payload)
        if record.get("transaction_hash") == SETTLEMENT_TX
    ]
    classic_outgoing = [
        record
        for record in matching
        if record.get("type") in _CLASSIC_PAYMENT_TYPES
        and (record.get("from") == ACCOUNT or record.get("source_account") == ACCOUNT)
        and record.get("asset_code") == "USDC"
        and record.get("asset_issuer") == USDC_ISSUER
    ]
    record_types = sorted(
        {
            str(record["type"])
            for record in matching
            if record.get("type") is not None
        }
    )
    return {
        "matching_transaction_record_count": len(matching),
        "matching_record_types": record_types,
        "candidate_classic_outgoing_usdc_count": len(classic_outgoing),
    }


def build_report() -> dict[str, Any]:
    settlement = _transaction_bundle(SETTLEMENT_TX)
    later_credit = _transaction_bundle(POST_INCIDENT_CREDIT_TX)
    payment_view = _account_payment_view()

    settlement_effects = _usdc_effects(settlement)
    debits = [
        effect
        for effect in settlement_effects
        if effect.get("type") == "account_debited" and effect.get("account") == ACCOUNT
    ]
    credits = [
        effect
        for effect in settlement_effects
        if effect.get("type") == "account_credited"
        and effect.get("account") == SETTLEMENT_DESTINATION
    ]

    if settlement["transaction"].get("successful") is not True:
        raise RuntimeError("the incident settlement transaction is not successful")
    if len(debits) != 1 or len(credits) != 1:
        raise RuntimeError("expected exactly one matching USDC debit and credit effect")

    debit_amount = _decimal(debits[0].get("amount"))
    credit_amount = _decimal(credits[0].get("amount"))
    if debit_amount != Decimal("35.8800000") or credit_amount != debit_amount:
        raise RuntimeError(
            f"unexpected settlement amounts: debit={debit_amount}, credit={credit_amount}"
        )

    later_usdc_effects = _usdc_effects(later_credit)
    later_wallet_credits = [
        effect
        for effect in later_usdc_effects
        if effect.get("type") == "account_credited" and effect.get("account") == ACCOUNT
    ]

    return {
        "probe": "asgcard-issue-17-stellar-mainnet-read-only",
        "probe_version": 3,
        "read_only": True,
        "network": "Stellar Mainnet",
        "horizon": HORIZON,
        "account": ACCOUNT,
        "incident_started_at": INCIDENT_STARTED_AT,
        "queried_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "settlement": settlement,
        "settlement_assertions": {
            "transaction_successful": True,
            "wallet_debit_count": len(debits),
            "destination_credit_count": len(credits),
            "debited_amount": str(debit_amount),
            "credited_amount": str(credit_amount),
        },
        "ledger_view_divergence": {
            "account_payments_candidate_outgoing_usdc_count": payment_view[
                "candidate_classic_outgoing_usdc_count"
            ],
            "account_payments_matching_record_types": payment_view[
                "matching_record_types"
            ],
            "account_effects_matching_debit_count": len(debits),
            "account_effects_matching_credit_count": len(credits),
        },
        "post_incident_credit": later_credit,
        "post_incident_assertions": {
            "wallet_credit_count": len(later_wallet_credits),
            "operation_binding": None,
            "classification": "unattributed_credit",
        },
        "claim_boundary": {
            "supports": [
                "The 35.88 USDC settlement transaction succeeded.",
                "The transaction effects contain a matching wallet debit and destination credit.",
                "The account payment view does not expose this Soroban settlement as a classic outgoing USDC payment.",
            ],
            "does_not_support": [
                "The downstream card issuer definitely failed.",
                "The later credit is definitely a refund for this operation.",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only public Horizon probe for ASG Card issue 17."
    )
    parser.add_argument(
        "--output",
        default="artifacts/asgcard_issue17_ledger_probe.json",
        help="Path for the sanitized JSON report.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        report = build_report()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, RuntimeError) as exc:
        print(f"ASG Card read-only probe failed: {type(exc).__name__}: {exc}")
        return 1

    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(output.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
