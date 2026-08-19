from __future__ import annotations

from .models import Invoice, PaymentPlan


class DeterministicPlanner:
    """Critical payment facts come from trusted invoice state, never free-form LLM output."""

    def plan(self, invoice: Invoice) -> PaymentPlan:
        return PaymentPlan(
            invoice_id=invoice.invoice_id,
            vendor_id=invoice.vendor_id,
            amount_cents=invoice.amount_cents,
            currency=invoice.currency,
            execution_key=f"pay:{invoice.invoice_id}:v1",
        )
