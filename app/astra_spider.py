"""Backward-compatible public import surface for the Astra verifier."""

from collections.abc import Iterable
from dataclasses import replace

from .astra_attribution import verify_settlement_attribution_outcome
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
    """Map a generic outcome claim only when finality is operation-bound.

    A failed business operation and a successful ledger movement can coexist.
    Reusing the base claim/finality invariant is safe only when authoritative
    payment finality carries the same ``operation_id`` as the generic claim, or
    when both sides are intentionally trace-global. Unbound candidate
    settlements are handled by the attribution verifier instead of being
    promoted into operation-level truth.
    """

    operation_bound_finality = {
        event.operation_id
        for event in events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key == "payment_status"
        and event.authoritative
        and event.operation_id is not None
    }
    has_global_finality = any(
        event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key == "payment_status"
        and event.authoritative
        and event.operation_id is None
        for event in events
    )

    normalized: list[StateEvent] = []
    for event in events:
        should_normalize = (
            event.stage == Stage.CLAIMED_RESULT
            and event.key in _CLAIM_STATUS_KEYS
            and (
                event.operation_id in operation_bound_finality
                if event.operation_id is not None
                else has_global_finality
            )
        )
        normalized.append(
            replace(event, key="payment_status") if should_normalize else event
        )
    return tuple(normalized)


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
        *verify_settlement_attribution_outcome(materialized),
    ]


__all__ = [
    "STATE_GRAPH",
    "Finding",
    "Stage",
    "StateEvent",
    "verify_causal_economic_outcome",
    "verify_indeterminate_retry_outcome",
    "verify_multileg_causal_outcome",
    "verify_settlement_attribution_outcome",
    "verify_terminal_commitment_outcome",
]
