from __future__ import annotations

from uuid import uuid4

from .models import DispatchResult, PaymentPlan
from .state import DemoState


class MockPaymentProvider:
    """Deterministic external-system simulator. No real money is moved.

    The idempotency lookup and execution-key claim happen under the same lock, so
    concurrent retries cannot both pass a check-then-write race.
    """

    def __init__(self, state: DemoState):
        self.state = state

    def dispatch(self, plan: PaymentPlan) -> DispatchResult:
        with self.state.lock:
            existing_id = self.state.execution_index.get(plan.execution_key)
            if existing_id:
                return DispatchResult(
                    accepted=True,
                    duplicate=True,
                    provider_payment_id=existing_id,
                    provider_status="DUPLICATE",
                    message="Execution key already consumed; no second payment created.",
                )

            if self.state.settlement_mode == "reject":
                return DispatchResult(
                    accepted=False,
                    provider_status="REJECTED",
                    message="Provider rejected the payment request.",
                )

            payment_id = f"PAY-{uuid4().hex[:8].upper()}"
            # Claim and side-effect decision are atomic with duplicate detection.
            self.state.execution_index[plan.execution_key] = payment_id

            if self.state.settlement_mode == "accept_without_settle":
                return DispatchResult(
                    accepted=True,
                    provider_payment_id=payment_id,
                    provider_status="ACCEPTED",
                    message="Provider accepted request but settlement is not visible in ledger.",
                )

            self.state.ledger[payment_id] = {
                "invoice_id": plan.invoice_id,
                "vendor_id": plan.vendor_id,
                "amount_cents": plan.amount_cents,
                "currency": plan.currency,
                "status": "SETTLED",
                "execution_key": plan.execution_key,
            }
            return DispatchResult(
                accepted=True,
                provider_payment_id=payment_id,
                provider_status="SETTLED",
                message="Provider settled payment and ledger state changed.",
            )

    def settle_pending(self, execution_key: str, plan: PaymentPlan) -> str | None:
        with self.state.lock:
            payment_id = self.state.execution_index.get(execution_key)
            if not payment_id:
                return None
            if payment_id not in self.state.ledger:
                self.state.ledger[payment_id] = {
                    "invoice_id": plan.invoice_id,
                    "vendor_id": plan.vendor_id,
                    "amount_cents": plan.amount_cents,
                    "currency": plan.currency,
                    "status": "SETTLED",
                    "execution_key": plan.execution_key,
                }
            return payment_id
