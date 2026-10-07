"""Synthetic offline evidence for a Decisions advisory pilot.

The corpus exercises the existing deterministic orchestrator with its mock
payment provider. It does not evaluate a model or write arena evidence logs.
Only ``model_evidence`` is eligible for a future model request; gold labels and
the sealed deterministic baseline remain local evaluation data.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models import Invoice, ReceiptStatus
from app.orchestrator import AgentProofOrchestrator
from app.state import DemoState


SCENARIOS = (
    "happy",
    "stale_policy",
    "expired_grant",
    "amount_drift",
    "vendor_drift",
    "currency_drift",
    "replay",
    "accepted_without_settlement",
    "unapproved_invoice",
    "partial_trace",
)

# These are authored synthetic labels, not observed Decisions predictions.
GOLD = {
    "happy": (False, "routine", 0),
    "stale_policy": (True, "authorization_gap", 2),
    "expired_grant": (True, "authorization_gap", 2),
    "amount_drift": (True, "integrity_gap", 3),
    "vendor_drift": (True, "integrity_gap", 3),
    "currency_drift": (True, "integrity_gap", 3),
    "replay": (True, "replay", 3),
    "accepted_without_settlement": (True, "insufficient_evidence", 2),
    "unapproved_invoice": (True, "authorization_gap", 2),
    "partial_trace": (True, "insufficient_evidence", 1),
}

EXPECTED_STATUS = {
    "happy": ReceiptStatus.VERIFIED,
    "stale_policy": ReceiptStatus.BLOCKED,
    "expired_grant": ReceiptStatus.BLOCKED,
    "amount_drift": ReceiptStatus.BLOCKED,
    "vendor_drift": ReceiptStatus.BLOCKED,
    "currency_drift": ReceiptStatus.BLOCKED,
    "replay": ReceiptStatus.DUPLICATE,
    "accepted_without_settlement": ReceiptStatus.UNVERIFIED,
    "unapproved_invoice": ReceiptStatus.BLOCKED,
    "partial_trace": ReceiptStatus.VERIFIED,
}

_VARIANTS = (
    ("01", "SYN-VENDOR-ALPHA", "Synthetic Alpha", 12_000, "USD"),
    ("02", "SYN-VENDOR-BETA", "Synthetic Beta", 27_550, "EUR"),
    ("03", "SYN-VENDOR-GAMMA", "Synthetic Gamma", 100_000, "TRY"),
)

_RECEIPT_FACT_FIELDS = (
    "actor",
    "action",
    "invoice_id",
    "vendor_id",
    "amount_cents",
    "currency",
    "execution_key",
    "policy_epoch_authorized",
    "policy_epoch_at_execution",
    "provider_payment_id",
)


def _run_scenario(scenario: str, invoice: Invoice):
    state = DemoState()
    orchestrator = AgentProofOrchestrator(state)
    hook = None
    if scenario == "stale_policy":
        hook = lambda current_state, plan: current_state.freeze_vendor(plan.vendor_id)
    elif scenario == "expired_grant":
        # Deterministic expiry without sleeping or changing global clocks.
        orchestrator.policy.ttl_seconds = -1
    elif scenario == "amount_drift":
        hook = lambda _state, plan: setattr(plan, "amount_cents", plan.amount_cents + 1)
    elif scenario == "vendor_drift":
        hook = lambda _state, plan: setattr(plan, "vendor_id", "SYN-UNAPPROVED-VENDOR")
    elif scenario == "currency_drift":
        hook = lambda _state, plan: setattr(
            plan, "currency", "EUR" if plan.currency != "EUR" else "USD"
        )
    elif scenario == "accepted_without_settlement":
        state.settlement_mode = "accept_without_settle"
    elif scenario == "unapproved_invoice":
        invoice.approved = False

    if scenario == "replay":
        orchestrator.process_invoice(invoice)
    receipt = orchestrator.process_invoice(invoice, before_dispatch=hook)
    if receipt.status != EXPECTED_STATUS[scenario]:
        raise RuntimeError(f"Synthetic scenario {scenario} changed its deterministic behavior")
    return state, receipt


def build_corpus() -> list[dict[str, Any]]:
    """Build 30 JSON-safe cases with stable IDs and isolated mock runtime state.

    Baseline receipts preserve their original seal, timestamp, outcome and
    status. Their volatile IDs/timestamps are deliberately not advertised as
    reproducible corpus bytes. ``partial_trace`` modifies only a deep copy of
    model evidence, leaving the original VERIFIED receipt intact.
    """
    cases = []
    for scenario in SCENARIOS:
        for variant, vendor_id, vendor_name, amount_cents, currency in _VARIANTS:
            invoice = Invoice(
                invoice_id=f"SYN-INVOICE-{variant}",
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                amount_cents=amount_cents,
                currency=currency,
            )
            state, receipt = _run_scenario(scenario, invoice)
            original = receipt.model_dump(mode="json")
            evidence_events = deepcopy(original["events"])
            if scenario == "partial_trace":
                evidence_events = [
                    event for event in evidence_events if event["stage"] != "outcome"
                ]
            needs_review, primary_signal, severity = GOLD[scenario]
            cases.append(
                {
                    "case_id": f"APD-{len(cases) + 1:03d}",
                    "scenario": scenario,
                    "variant": variant,
                    "provenance": "synthetic-agentproof-mock-provider",
                    "model_evidence": {
                        "invoice": invoice.model_dump(mode="json"),
                        "receipt_facts": {
                            field: deepcopy(original[field]) for field in _RECEIPT_FACT_FIELDS
                        },
                        "events": evidence_events,
                    },
                    "gold": {
                        "needs_review": needs_review,
                        "primary_signal": primary_signal,
                        "severity": severity,
                    },
                    "baseline": {
                        "receipt": original,
                        "verdict": original["status"],
                        "ledger_count": state.ledger_count(),
                        "money_moved_cents": state.total_money_moved_cents(),
                    },
                }
            )
    return cases
