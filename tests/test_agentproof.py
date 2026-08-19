from concurrent.futures import ThreadPoolExecutor

from app.demo import DEFAULT_INVOICE
from app.evidence import verify_receipt_integrity
from app.models import ReceiptStatus
from app.orchestrator import AgentProofOrchestrator
from app.state import DemoState


def runtime():
    state = DemoState()
    return state, AgentProofOrchestrator(state)


def test_happy_path_requires_real_state_transition():
    state, orchestrator = runtime()
    receipt = orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))
    assert receipt.status == ReceiptStatus.VERIFIED
    assert receipt.claimed_success is True
    assert state.ledger_count() == 1
    assert verify_receipt_integrity(receipt)


def test_stale_authorization_is_blocked_at_execution_seam():
    state, orchestrator = runtime()

    def world_changes(s, plan):
        s.freeze_vendor(plan.vendor_id)

    receipt = orchestrator.process_invoice(
        DEFAULT_INVOICE.model_copy(deep=True), before_dispatch=world_changes
    )
    assert receipt.status == ReceiptStatus.BLOCKED
    assert receipt.claimed_success is False
    assert state.ledger_count() == 0
    assert any("stale" in e.message.lower() for e in receipt.events)


def test_action_mutation_after_authorization_is_blocked():
    state, orchestrator = runtime()

    def tamper_with_plan(_state, plan):
        plan.amount_cents = 9_999_999

    receipt = orchestrator.process_invoice(
        DEFAULT_INVOICE.model_copy(deep=True), before_dispatch=tamper_with_plan
    )
    assert receipt.status == ReceiptStatus.BLOCKED
    assert receipt.claimed_success is False
    assert state.ledger_count() == 0
    assert any("authorized action" in e.message.lower() for e in receipt.events)


def test_provider_acceptance_cannot_be_recorded_as_success_without_settlement():
    state, orchestrator = runtime()
    state.settlement_mode = "accept_without_settle"
    receipt = orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))
    assert receipt.status == ReceiptStatus.UNVERIFIED
    assert receipt.claimed_success is False
    assert state.ledger_count() == 0
    assert any(e.stage == "outcome" and e.status == "UNVERIFIED" for e in receipt.events)


def test_replay_cannot_create_second_payment():
    state, orchestrator = runtime()
    invoice = DEFAULT_INVOICE.model_copy(deep=True)
    first = orchestrator.process_invoice(invoice)
    second = orchestrator.process_invoice(invoice)
    assert first.status == ReceiptStatus.VERIFIED
    assert second.status == ReceiptStatus.DUPLICATE
    assert second.claimed_success is False
    assert state.ledger_count() == 1


def test_concurrent_replays_are_atomic_at_most_once():
    state, orchestrator = runtime()

    def attempt(_):
        return orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))

    with ThreadPoolExecutor(max_workers=12) as pool:
        receipts = list(pool.map(attempt, range(24)))

    assert sum(r.status == ReceiptStatus.VERIFIED for r in receipts) == 1
    assert sum(r.status == ReceiptStatus.DUPLICATE for r in receipts) == 23
    assert state.ledger_count() == 1
    assert state.total_money_moved_cents() == 5_000_000


def test_receipt_tampering_is_detectable():
    state, orchestrator = runtime()
    receipt = orchestrator.process_invoice(DEFAULT_INVOICE.model_copy(deep=True))
    assert verify_receipt_integrity(receipt)
    receipt.amount_cents = 1
    assert not verify_receipt_integrity(receipt)
