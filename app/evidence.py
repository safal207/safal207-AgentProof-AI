from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from .models import EvidenceEvent, ExecutionReceipt, PaymentPlan, ReceiptStatus


def now_event(stage: str, status: str, message: str, **data) -> EvidenceEvent:
    return EvidenceEvent(
        stage=stage,
        status=status,
        message=message,
        at=datetime.now(timezone.utc),
        data=data,
    )


def seal_receipt(receipt: ExecutionReceipt) -> ExecutionReceipt:
    payload = receipt.model_dump(mode="json")
    payload["evidence_hash"] = ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    receipt.evidence_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return receipt


def verify_receipt_integrity(receipt: ExecutionReceipt) -> bool:
    expected = receipt.evidence_hash
    clone = receipt.model_copy(deep=True)
    clone.evidence_hash = ""
    payload = clone.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return expected == actual


def new_receipt(
    plan: PaymentPlan,
    status: ReceiptStatus,
    policy_epoch_at_execution: int,
    events: list[EvidenceEvent],
    policy_epoch_authorized: int | None = None,
    provider_payment_id: str | None = None,
    claimed_success: bool = False,
) -> ExecutionReceipt:
    return seal_receipt(
        ExecutionReceipt(
            receipt_id=f"APR-{uuid4().hex[:10].upper()}",
            status=status,
            actor=plan.actor,
            action=plan.action,
            invoice_id=plan.invoice_id,
            vendor_id=plan.vendor_id,
            amount_cents=plan.amount_cents,
            currency=plan.currency,
            execution_key=plan.execution_key,
            policy_epoch_authorized=policy_epoch_authorized,
            policy_epoch_at_execution=policy_epoch_at_execution,
            provider_payment_id=provider_payment_id,
            claimed_success=claimed_success,
            events=events,
            created_at=datetime.now(timezone.utc),
        )
    )
