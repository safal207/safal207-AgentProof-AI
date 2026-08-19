from __future__ import annotations

from .models import Invoice
from .orchestrator import AgentProofOrchestrator
from .state import DemoState


DEFAULT_INVOICE = Invoice(
    invoice_id="7842",
    vendor_id="acme",
    vendor_name="ACME Corp",
    amount_cents=5_000_000,
    currency="USD",
    approved=True,
)


class DemoRuntime:
    def __init__(self):
        self.state = DemoState()
        self.orchestrator = AgentProofOrchestrator(self.state)

    def reset(self) -> None:
        self.state.reset()

    def run(self, scenario: str):
        self.reset()
        invoice = DEFAULT_INVOICE.model_copy(deep=True)

        if scenario == "happy":
            receipt = self.orchestrator.process_invoice(invoice)
        elif scenario == "stale":
            def freeze_between_auth_and_dispatch(state, plan):
                state.freeze_vendor(plan.vendor_id)
            receipt = self.orchestrator.process_invoice(invoice, before_dispatch=freeze_between_auth_and_dispatch)
        elif scenario == "false-success":
            self.state.settlement_mode = "accept_without_settle"
            receipt = self.orchestrator.process_invoice(invoice)
        elif scenario == "replay":
            first = self.orchestrator.process_invoice(invoice)
            second = self.orchestrator.process_invoice(invoice)
            return {
                "scenario": scenario,
                "headline": "Duplicate execution blocked",
                "first": first.model_dump(mode="json"),
                "receipt": second.model_dump(mode="json"),
                "ledger_count": self.state.ledger_count(),
                "money_moved_cents": self.state.total_money_moved_cents(),
            }
        else:
            raise ValueError(f"Unknown scenario: {scenario}")

        headlines = {
            "happy": "Payment verified end-to-end",
            "stale": "$50,000 stale authorization blocked",
            "false-success": "Accepted is not settled: false success refused",
        }
        return {
            "scenario": scenario,
            "headline": headlines[scenario],
            "receipt": receipt.model_dump(mode="json"),
            "ledger_count": self.state.ledger_count(),
            "money_moved_cents": self.state.total_money_moved_cents(),
        }


demo_runtime = DemoRuntime()
