"""Backward-compatible public import surface for the Astra verifier."""

from .astra_verifier import (
    STATE_GRAPH,
    Finding,
    Stage,
    StateEvent,
    verify_causal_economic_outcome,
)

__all__ = [
    "STATE_GRAPH",
    "Finding",
    "Stage",
    "StateEvent",
    "verify_causal_economic_outcome",
]
