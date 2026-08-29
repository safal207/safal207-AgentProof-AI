"""Backward-compatible public import surface for the Astra verifier."""

from collections.abc import Iterable

from .astra_commitment import verify_terminal_commitment_outcome
from .astra_multileg import verify_multileg_causal_outcome
from .astra_verifier import (
    STATE_GRAPH,
    Finding,
    Stage,
    StateEvent,
    verify_causal_economic_outcome as _verify_base_causal_economic_outcome,
)


def verify_causal_economic_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Run the protocol-neutral core and specialized lifecycle verifiers."""

    materialized = tuple(events)
    return [
        *_verify_base_causal_economic_outcome(materialized),
        *verify_multileg_causal_outcome(materialized),
        *verify_terminal_commitment_outcome(materialized),
    ]


__all__ = [
    "STATE_GRAPH",
    "Finding",
    "Stage",
    "StateEvent",
    "verify_causal_economic_outcome",
    "verify_multileg_causal_outcome",
    "verify_terminal_commitment_outcome",
]
