from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .models import AuthorizationGrant, Invoice, PaymentPlan
from .state import DemoState


class PolicyDenied(Exception):
    pass


class PolicyEngine:
    """Issues short-lived grants and revalidates them at the dispatch seam."""

    def __init__(self, state: DemoState, ttl_seconds: int = 60):
        self.state = state
        self.ttl_seconds = ttl_seconds

    def authorize(self, invoice: Invoice, plan: PaymentPlan) -> AuthorizationGrant:
        if not invoice.approved:
            raise PolicyDenied("Invoice is not approved.")
        if invoice.vendor_id in self.state.frozen_vendors:
            raise PolicyDenied("Vendor is frozen by current policy.")
        if (
            plan.invoice_id != invoice.invoice_id
            or plan.vendor_id != invoice.vendor_id
            or plan.amount_cents != invoice.amount_cents
            or plan.currency != invoice.currency
        ):
            raise PolicyDenied("Planned payment does not match approved invoice facts.")

        now = datetime.now(timezone.utc)
        return AuthorizationGrant(
            grant_id=f"grant-{uuid4().hex[:12]}",
            actor=plan.actor,
            action=plan.action,
            invoice_id=plan.invoice_id,
            vendor_id=plan.vendor_id,
            amount_cents=plan.amount_cents,
            currency=plan.currency,
            policy_epoch=self.state.policy_epoch,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            execution_key=plan.execution_key,
        )

    def verify_at_execution(self, grant: AuthorizationGrant, plan: PaymentPlan) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        if now >= grant.expires_at:
            return False, "Authorization expired before execution."
        if grant.policy_epoch != self.state.policy_epoch:
            return False, (
                f"Authorization is stale: policy epoch {grant.policy_epoch} -> "
                f"{self.state.policy_epoch}."
            )
        if grant.vendor_id in self.state.frozen_vendors:
            return False, "Vendor is frozen at the point of execution."
        if (
            grant.actor != plan.actor
            or grant.action != plan.action
            or grant.invoice_id != plan.invoice_id
            or grant.vendor_id != plan.vendor_id
            or grant.amount_cents != plan.amount_cents
            or grant.currency != plan.currency
            or grant.execution_key != plan.execution_key
        ):
            return False, "Execution no longer matches the authorized action."
        return True, "Authorization is fresh and action-bound at execution."
