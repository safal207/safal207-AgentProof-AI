from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

from .models import Invoice


SettlementMode = Literal["success", "accept_without_settle", "reject"]


@dataclass
class DemoState:
    """Mutable demo world state with one lock protecting execution-critical state."""

    policy_epoch: int = 17
    frozen_vendors: set[str] = field(default_factory=set)
    invoices: dict[str, Invoice] = field(default_factory=dict)
    ledger: dict[str, dict] = field(default_factory=dict)
    execution_index: dict[str, str] = field(default_factory=dict)
    settlement_mode: SettlementMode = "success"
    lock: RLock = field(default_factory=RLock, repr=False)

    def reset(self) -> None:
        with self.lock:
            self.policy_epoch = 17
            self.frozen_vendors.clear()
            self.invoices.clear()
            self.ledger.clear()
            self.execution_index.clear()
            self.settlement_mode = "success"

    def add_invoice(self, invoice: Invoice) -> None:
        with self.lock:
            self.invoices[invoice.invoice_id] = invoice

    def freeze_vendor(self, vendor_id: str) -> int:
        with self.lock:
            self.frozen_vendors.add(vendor_id)
            self.policy_epoch += 1
            return self.policy_epoch

    def unfreeze_vendor(self, vendor_id: str) -> int:
        with self.lock:
            self.frozen_vendors.discard(vendor_id)
            self.policy_epoch += 1
            return self.policy_epoch

    def ledger_count(self) -> int:
        with self.lock:
            return len(self.ledger)

    def total_money_moved_cents(self) -> int:
        with self.lock:
            return sum(row["amount_cents"] for row in self.ledger.values())
