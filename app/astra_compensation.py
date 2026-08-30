from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .astra_verifier import Finding, Stage, StateEvent


COMPENSABLE_FINDING_CODES = frozenset(
    {
        "CLAIMED_FAILED_BUT_SETTLED",
        "SETTLED_BUT_NOT_DELIVERED",
        "SETTLED_FULFILLMENT_FAILED",
    }
)

_SETTLED_STATUSES = frozenset(
    {
        "captured",
        "complete",
        "completed",
        "confirmed",
        "paid",
        "settled",
        "success",
        "succeeded",
    }
)

_REFUNDED_STATUSES = frozenset(
    {
        "compensated",
        "fully_refunded",
        "refunded",
        "settled",
        "success",
        "succeeded",
    }
)

_BOUND_STATUSES = frozenset({"authoritative", "bound", "verified"})
_UNRESOLVED_MARKERS = frozenset(
    {
        "candidate",
        "correlated",
        "high_contextual",
        "partial",
        "unbound",
        "unknown",
        "unresolved",
    }
)
_TERMINAL_RECONCILIATION = frozenset(
    {
        "compensated",
        "fully_refunded",
        "refunded",
    }
)


def _status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            parsed = value
        elif isinstance(value, int):
            parsed = Decimal(value)
        elif isinstance(value, float):
            parsed = Decimal(str(value))
        elif isinstance(value, str):
            parsed = Decimal(value.strip())
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _latest(
    events: list[StateEvent],
    *,
    stage: Stage,
    keys: set[str],
    operation_id: str | None = None,
    payment_id: str | None = None,
    authoritative: bool | None = None,
) -> StateEvent | None:
    matches = [
        event
        for event in events
        if event.stage == stage
        and event.key in keys
        and (operation_id is None or event.operation_id == operation_id)
        and (payment_id is None or event.payment_id == payment_id)
        and (authoritative is None or event.authoritative is authoritative)
    ]
    return matches[-1] if matches else None


def _event_index(events: list[StateEvent], target: StateEvent | None) -> int | None:
    if target is None:
        return None
    for index in range(len(events) - 1, -1, -1):
        if events[index] is target:
            return index
    return None


def _binding_disposition(event: StateEvent) -> tuple[str | None, str | None, str | None]:
    if not isinstance(event.value, Mapping):
        return None, None, None
    payment_id = event.value.get("payment_id")
    return (
        _status(event.value.get("status")),
        _status(event.value.get("confidence")),
        payment_id if isinstance(payment_id, str) and payment_id else None,
    )


def _terminal_refund_bindings(
    events: list[StateEvent],
    operation_id: str,
) -> dict[str, StateEvent]:
    latest_by_payment: dict[str, StateEvent] = {}
    for event in events:
        if event.stage != Stage.RECONCILIATION:
            continue
        if event.key != "refund_operation_binding":
            continue
        if event.operation_id != operation_id:
            continue
        if not event.authoritative:
            continue

        status, confidence, payment_id = _binding_disposition(event)
        if payment_id is None:
            continue
        if (
            status not in _BOUND_STATUSES
            and status not in _UNRESOLVED_MARKERS
            and confidence not in _UNRESOLVED_MARKERS
        ):
            continue
        latest_by_payment[payment_id] = event

    terminal: dict[str, StateEvent] = {}
    for payment_id, event in latest_by_payment.items():
        status, confidence, _ = _binding_disposition(event)
        if status in _BOUND_STATUSES and confidence not in _UNRESOLVED_MARKERS:
            terminal[payment_id] = event
    return terminal


def _authoritative_original_payment_id(
    events: list[StateEvent],
    operation_id: str,
) -> str | None:
    payment_ids = {
        event.payment_id
        for event in events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key == "payment_status"
        and event.operation_id == operation_id
        and event.authoritative
        and _status(event.value) in _SETTLED_STATUSES
        and event.payment_id
    }
    return next(iter(payment_ids)) if len(payment_ids) == 1 else None


def _operation_is_fully_compensated(
    events: list[StateEvent],
    operation_id: str,
) -> bool:
    original_payment_id = _authoritative_original_payment_id(events, operation_id)
    if original_payment_id is None:
        return False

    original_status_event = _latest(
        events,
        stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
        keys={"payment_status"},
        operation_id=operation_id,
        payment_id=original_payment_id,
        authoritative=True,
    )
    original_amount_event = _latest(
        events,
        stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
        keys={"settled_amount_minor"},
        operation_id=operation_id,
        payment_id=original_payment_id,
        authoritative=True,
    )
    original_asset_event = _latest(
        events,
        stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
        keys={"settlement_asset"},
        operation_id=operation_id,
        payment_id=original_payment_id,
        authoritative=True,
    )
    original_amount = _decimal(original_amount_event.value) if original_amount_event else None
    original_asset = (
        original_asset_event.value.strip()
        if original_asset_event
        and isinstance(original_asset_event.value, str)
        and original_asset_event.value.strip()
        else None
    )
    original_indices = [
        _event_index(events, original_status_event),
        _event_index(events, original_amount_event),
        _event_index(events, original_asset_event),
    ]
    if (
        original_amount is None
        or original_amount <= 0
        or original_asset is None
        or any(index is None for index in original_indices)
    ):
        return False
    original_complete_index = max(
        index for index in original_indices if index is not None
    )

    bindings = _terminal_refund_bindings(events, operation_id)
    if not bindings:
        return False

    refund_total = Decimal(0)
    latest_binding_index = -1
    for refund_payment_id, binding in bindings.items():
        refund_status = _latest(
            events,
            stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
            keys={"candidate_refund_status", "refund_status", "unbound_refund_status"},
            payment_id=refund_payment_id,
            authoritative=True,
        )
        refund_amount_event = _latest(
            events,
            stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
            keys={"refunded_amount_minor"},
            payment_id=refund_payment_id,
            authoritative=True,
        )
        refund_asset_event = _latest(
            events,
            stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
            keys={"refund_asset"},
            payment_id=refund_payment_id,
            authoritative=True,
        )

        refund_amount = _decimal(refund_amount_event.value) if refund_amount_event else None
        refund_asset = (
            refund_asset_event.value.strip()
            if refund_asset_event
            and isinstance(refund_asset_event.value, str)
            and refund_asset_event.value.strip()
            else None
        )
        refund_indices = [
            _event_index(events, refund_status),
            _event_index(events, refund_amount_event),
            _event_index(events, refund_asset_event),
        ]
        binding_index = _event_index(events, binding)
        if refund_status is None or _status(refund_status.value) not in _REFUNDED_STATUSES:
            return False
        if refund_amount is None or refund_amount <= 0:
            return False
        if refund_asset != original_asset:
            return False
        if binding_index is None or any(index is None for index in refund_indices):
            return False

        refund_complete_index = max(
            index for index in refund_indices if index is not None
        )
        if refund_complete_index <= original_complete_index:
            return False
        if binding_index <= refund_complete_index:
            return False

        latest_binding_index = max(latest_binding_index, binding_index)
        refund_total += refund_amount

    if refund_total != original_amount:
        return False

    reconciliation = _latest(
        events,
        stage=Stage.RECONCILIATION,
        keys={"status"},
        operation_id=operation_id,
        authoritative=True,
    )
    reconciliation_index = _event_index(events, reconciliation)
    return bool(
        reconciliation
        and reconciliation_index is not None
        and reconciliation_index > latest_binding_index
        and _status(reconciliation.value) in _TERMINAL_RECONCILIATION
    )


def is_fully_compensated(
    events: Iterable[StateEvent],
    findings: Iterable[Finding],
) -> bool:
    """Return whether every divergence is fully and terminally compensated.

    Compensation is deliberately narrower than generic recovery. Historical
    findings remain in the report. This function only changes the top-level
    verdict when every finding belongs to the initial compensable set and each
    affected operation has exact-value, same-asset, independently finalized,
    authoritatively bound, terminally reconciled refund evidence.
    """

    materialized_events = list(events)
    materialized_findings = list(findings)
    if not materialized_findings:
        return False
    if any(
        finding.code not in COMPENSABLE_FINDING_CODES
        for finding in materialized_findings
    ):
        return False

    operation_ids = {finding.operation_id for finding in materialized_findings}
    if not operation_ids or None in operation_ids or "" in operation_ids:
        return False

    return all(
        _operation_is_fully_compensated(materialized_events, operation_id)
        for operation_id in operation_ids
        if operation_id is not None
    )
