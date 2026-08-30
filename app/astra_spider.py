"""Backward-compatible public import surface for the Astra verifier."""

from collections.abc import Iterable
from dataclasses import replace

from .astra_commitment import verify_terminal_commitment_outcome
from .astra_multileg import verify_multileg_causal_outcome
from .astra_retry import verify_indeterminate_retry_outcome
from .astra_verifier import (
    STATE_GRAPH,
    Finding,
    Stage,
    StateEvent,
    verify_causal_economic_outcome as _verify_base_causal_economic_outcome,
)


_CLAIM_STATUS_KEYS = frozenset({"operation_status", "outcome_status"})


def _normalize_generic_outcome_claims(
    events: tuple[StateEvent, ...],
) -> tuple[StateEvent, ...]:
    """Map generic terminal outcome claims onto the base claim/finality edge.

    The protocol-neutral state graph permits a component to claim that the
    overall operation failed without claiming that payment itself failed. The
    base verifier historically used ``payment_status`` as its terminal claim
    key. Normalizing only the key preserves that semantic distinction in the
    source evidence while reusing the same adjacent-state invariant:
    a failed claimed outcome can still conflict with authoritative settlement.
    """

    return tuple(
        replace(event, key="payment_status")
        if event.stage == Stage.CLAIMED_RESULT and event.key in _CLAIM_STATUS_KEYS
        else event
        for event in events
    )


def verify_causal_economic_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Run the protocol-neutral core and specialized lifecycle verifiers."""

    materialized = tuple(events)
    normalized = _normalize_generic_outcome_claims(materialized)
    return [
        *_verify_base_causal_economic_outcome(normalized),
        *verify_multileg_causal_outcome(materialized),
        *verify_terminal_commitment_outcome(materialized),
        *verify_indeterminate_retry_outcome(materialized),
    ]


__all__ = [
    "STATE_GRAPH",
    "Finding",
    "Stage",
    "StateEvent",
    "verify_causal_economic_outcome",
    "verify_indeterminate_retry_outcome",
    "verify_multileg_causal_outcome",
    "verify_terminal_commitment_outcome",
]
