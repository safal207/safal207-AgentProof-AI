from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .astra_verifier import Stage, StateEvent


CommitmentTiming = Literal["before_settlement", "after_settlement"]


@dataclass(frozen=True)
class AP2AttemptObservation:
    attempt_id: str
    authorization_id: str
    claimed_status: str
    psp_status: str
    token_used: bool = False
    order_id: str | None = None
    next_action: str | None = None
    payment_id: str | None = None
    commitment_timing: CommitmentTiming = "before_settlement"


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _commitment_events(
    *,
    operation_id: str,
    observation: AP2AttemptObservation,
) -> list[StateEvent]:
    events: list[StateEvent] = []
    if observation.token_used:
        events.append(
            StateEvent(
                stage=Stage.PAYMENT_ATTEMPT,
                key="terminal_commitment",
                value={
                    "component": "payment_token",
                    "status": "used",
                    "terminal": True,
                },
                source="ap2-token-store",
                authoritative=True,
                operation_id=operation_id,
                attempt_id=observation.attempt_id,
                authorization_id=observation.authorization_id,
            )
        )
    if observation.order_id:
        events.append(
            StateEvent(
                stage=Stage.PAYMENT_ATTEMPT,
                key="terminal_commitment",
                value={
                    "component": "order",
                    "status": "allocated",
                    "terminal": True,
                    "reference": observation.order_id,
                },
                source="ap2-order-store",
                authoritative=True,
                operation_id=operation_id,
                attempt_id=observation.attempt_id,
                authorization_id=observation.authorization_id,
            )
        )
    return events


def normalize_ap2_checkout(
    *,
    operation_id: str,
    attempts: Sequence[AP2AttemptObservation],
    receipt_status: str | None = None,
    delivery_status: str | None = None,
    reconciliation_status: str | None = None,
) -> tuple[StateEvent, ...]:
    """Normalize AP2 checkout observations into the Astra state graph.

    ``psp_status`` must come from an independently designated PSP/chain/test
    harness source. Local token and order stores remain authoritative only
    about their own state and are emitted separately as terminal commitments.
    """

    operation_id = _required_text(operation_id, "operation_id")
    if not attempts:
        raise ValueError("attempts must contain at least one observation")

    events: list[StateEvent] = [
        StateEvent(
            stage=Stage.QUOTE_CHALLENGE,
            key="requires_settlement_before_terminal_commitment",
            value=True,
            source="ap2-checkout-contract",
            operation_id=operation_id,
        ),
        StateEvent(
            stage=Stage.MANDATE_AUTHORIZATION,
            key="expected_settlement_count",
            value=1,
            source="ap2-checkout-contract",
            operation_id=operation_id,
        ),
    ]

    for observation in attempts:
        attempt_id = _required_text(observation.attempt_id, "attempt_id")
        authorization_id = _required_text(
            observation.authorization_id,
            "authorization_id",
        )
        if observation.commitment_timing not in {
            "before_settlement",
            "after_settlement",
        }:
            raise ValueError(
                "commitment_timing must be before_settlement or after_settlement"
            )

        events.append(
            StateEvent(
                stage=Stage.PAYMENT_ATTEMPT,
                key="attempt",
                value="submitted",
                source="ap2-merchant-agent",
                operation_id=operation_id,
                attempt_id=attempt_id,
                authorization_id=authorization_id,
            )
        )

        commitments = _commitment_events(
            operation_id=operation_id,
            observation=observation,
        )
        if observation.commitment_timing == "before_settlement":
            events.extend(commitments)

        events.extend(
            [
                StateEvent(
                    stage=Stage.CLAIMED_RESULT,
                    key="payment_status",
                    value=_required_text(
                        observation.claimed_status,
                        "claimed_status",
                    ),
                    source="ap2-merchant-agent",
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    authorization_id=authorization_id,
                ),
                StateEvent(
                    stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    key="payment_status",
                    value=_required_text(observation.psp_status, "psp_status"),
                    source="independent-psp-or-chain-evidence",
                    authoritative=True,
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    authorization_id=authorization_id,
                    payment_id=observation.payment_id,
                ),
            ]
        )

        if observation.commitment_timing == "after_settlement":
            events.extend(commitments)

        if observation.next_action is not None:
            events.append(
                StateEvent(
                    stage=Stage.CLAIMED_RESULT,
                    key="next_action",
                    value=_required_text(observation.next_action, "next_action"),
                    source="ap2-merchant-agent",
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    authorization_id=authorization_id,
                )
            )

    if receipt_status is not None:
        events.append(
            StateEvent(
                stage=Stage.RECEIPT,
                key="payment_status",
                value=_required_text(receipt_status, "receipt_status"),
                source="ap2-checkout-receipt",
                operation_id=operation_id,
            )
        )
    if delivery_status is not None:
        events.append(
            StateEvent(
                stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                key="delivery_status",
                value=_required_text(delivery_status, "delivery_status"),
                source="ap2-merchant-delivery",
                operation_id=operation_id,
            )
        )
    if reconciliation_status is not None:
        events.append(
            StateEvent(
                stage=Stage.RECONCILIATION,
                key="status",
                value=_required_text(
                    reconciliation_status,
                    "reconciliation_status",
                ),
                source="ap2-reconciliation-ledger",
                operation_id=operation_id,
            )
        )

    return tuple(events)


__all__ = [
    "AP2AttemptObservation",
    "CommitmentTiming",
    "normalize_ap2_checkout",
]
