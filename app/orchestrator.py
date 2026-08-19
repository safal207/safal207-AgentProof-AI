from __future__ import annotations

from collections.abc import Callable

from .evidence import new_receipt, now_event
from .models import ExecutionReceipt, Invoice, PaymentPlan, ReceiptStatus
from .payment_provider import MockPaymentProvider
from .planner import DeterministicPlanner
from .policy import PolicyDenied, PolicyEngine
from .state import DemoState


BeforeDispatchHook = Callable[[DemoState, PaymentPlan], None]


class AgentProofOrchestrator:
    """The decisive separation: plan -> authorize -> revalidate -> dispatch -> verify outcome."""

    def __init__(self, state: DemoState):
        self.state = state
        self.planner = DeterministicPlanner()
        self.policy = PolicyEngine(state)
        self.provider = MockPaymentProvider(state)

    def process_invoice(
        self,
        invoice: Invoice,
        *,
        before_dispatch: BeforeDispatchHook | None = None,
    ) -> ExecutionReceipt:
        self.state.add_invoice(invoice)
        plan = self.planner.plan(invoice)
        events = [
            now_event("intent", "OBSERVED", "Invoice event received by autonomous payment agent."),
            now_event("plan", "PROPOSED", "Agent proposed a payment action from trusted invoice facts.", execution_key=plan.execution_key),
        ]

        try:
            grant = self.policy.authorize(invoice, plan)
        except PolicyDenied as exc:
            events.append(now_event("authority", "BLOCKED", str(exc)))
            return new_receipt(
                plan,
                ReceiptStatus.BLOCKED,
                self.state.policy_epoch,
                events,
            )

        events.append(
            now_event(
                "authority",
                "AUTHORIZED",
                "Short-lived action-bound grant issued.",
                grant_id=grant.grant_id,
                policy_epoch=grant.policy_epoch,
            )
        )

        # Demo/test seam: the world may change after authorization but before dispatch.
        if before_dispatch:
            before_dispatch(self.state, plan)
            events.append(
                now_event(
                    "world_state",
                    "CHANGED",
                    "External policy/state changed after planning and before dispatch.",
                    policy_epoch=self.state.policy_epoch,
                )
            )

        fresh, reason = self.policy.verify_at_execution(grant, plan)
        if not fresh:
            events.append(now_event("execution_gate", "BLOCKED", reason))
            return new_receipt(
                plan,
                ReceiptStatus.BLOCKED,
                self.state.policy_epoch,
                events,
                policy_epoch_authorized=grant.policy_epoch,
            )

        events.append(now_event("execution_gate", "PASS", reason))
        dispatch = self.provider.dispatch(plan)

        if dispatch.duplicate:
            events.append(
                now_event(
                    "dispatch",
                    "DUPLICATE_BLOCKED",
                    dispatch.message,
                    provider_payment_id=dispatch.provider_payment_id,
                )
            )
            return new_receipt(
                plan,
                ReceiptStatus.DUPLICATE,
                self.state.policy_epoch,
                events,
                policy_epoch_authorized=grant.policy_epoch,
                provider_payment_id=dispatch.provider_payment_id,
                claimed_success=False,
            )

        if not dispatch.accepted:
            events.append(now_event("dispatch", "REJECTED", dispatch.message))
            return new_receipt(
                plan,
                ReceiptStatus.UNVERIFIED,
                self.state.policy_epoch,
                events,
                policy_epoch_authorized=grant.policy_epoch,
                claimed_success=False,
            )

        events.append(
            now_event(
                "dispatch",
                dispatch.provider_status,
                dispatch.message,
                provider_payment_id=dispatch.provider_payment_id,
            )
        )

        # Outcome grounding: accepted/202 is NOT success. Verify observable state.
        ledger_row = self.state.ledger.get(dispatch.provider_payment_id or "")
        settled = bool(
            ledger_row
            and ledger_row.get("status") == "SETTLED"
            and ledger_row.get("invoice_id") == plan.invoice_id
            and ledger_row.get("vendor_id") == plan.vendor_id
            and ledger_row.get("amount_cents") == plan.amount_cents
            and ledger_row.get("currency") == plan.currency
        )
        if not settled:
            events.append(
                now_event(
                    "outcome",
                    "UNVERIFIED",
                    "Dispatch was accepted, but the expected ledger state transition is absent.",
                )
            )
            return new_receipt(
                plan,
                ReceiptStatus.UNVERIFIED,
                self.state.policy_epoch,
                events,
                policy_epoch_authorized=grant.policy_epoch,
                provider_payment_id=dispatch.provider_payment_id,
                claimed_success=False,
            )

        events.append(
            now_event(
                "outcome",
                "VERIFIED",
                "Ledger proves the intended state transition occurred exactly once.",
                ledger_status=ledger_row["status"],
            )
        )
        return new_receipt(
            plan,
            ReceiptStatus.VERIFIED,
            self.state.policy_epoch,
            events,
            policy_epoch_authorized=grant.policy_epoch,
            provider_payment_id=dispatch.provider_payment_id,
            claimed_success=True,
        )
