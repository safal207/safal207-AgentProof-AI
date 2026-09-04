"""Backward-compatible public surface for settlement-gated delivery checks."""

from .astra_settlement_gate_core import verify_settlement_gated_delivery


__all__ = ["verify_settlement_gated_delivery"]
