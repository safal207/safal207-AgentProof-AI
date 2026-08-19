from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReceiptStatus(str, Enum):
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"
    DUPLICATE = "DUPLICATE"


class Invoice(BaseModel):
    invoice_id: str
    vendor_id: str
    vendor_name: str
    amount_cents: int = Field(gt=0)
    currency: str = "USD"
    approved: bool = True


class PaymentPlan(BaseModel):
    actor: str = "vendor-payment-agent"
    action: str = "pay_invoice"
    invoice_id: str
    vendor_id: str
    amount_cents: int
    currency: str
    execution_key: str
    rationale: str = "Approved invoice matched payment policy."


class AuthorizationGrant(BaseModel):
    grant_id: str
    actor: str
    action: str
    invoice_id: str
    vendor_id: str
    amount_cents: int
    currency: str
    policy_epoch: int
    issued_at: datetime
    expires_at: datetime
    execution_key: str


class DispatchResult(BaseModel):
    accepted: bool
    duplicate: bool = False
    provider_payment_id: str | None = None
    provider_status: str
    message: str


class EvidenceEvent(BaseModel):
    stage: str
    status: str
    message: str
    at: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class ExecutionReceipt(BaseModel):
    receipt_id: str
    status: ReceiptStatus
    actor: str
    action: str
    invoice_id: str
    vendor_id: str
    amount_cents: int
    currency: str
    execution_key: str
    policy_epoch_authorized: int | None = None
    policy_epoch_at_execution: int
    provider_payment_id: str | None = None
    claimed_success: bool = False
    events: list[EvidenceEvent] = Field(default_factory=list)
    created_at: datetime
    evidence_hash: str = ""
