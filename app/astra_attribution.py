from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .astra_verifier import Finding, Stage, StateEvent


_SETTLED_STATUSES = {
    "captured",
    "complete",
    "completed",
    "confirmed",
    "paid",
    "settled",
    "success",
    "succeeded",
}

_BOUND_BINDING_STATUSES = {
    "authoritative",
    "bound",
    "verified",
}

_UNRESOLVED_BINDING_STATUSES = {
    "candidate",
    "correlated",
    "high_contextual",
    "unbound",
    "unknown",
    "unresolved",
}


def _status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _finding(
    *,
    explanation: str,
    operation_id: str | None,
    evidence: Iterable[StateEvent | None],
) -> Finding:
    sources = tuple(
        dict.fromkeys(
            event.source
            for event in evidence
            if event is not None and event.source
        )
    )
    return Finding(
        code="SETTLEMENT_OPERATION_BINDING_UNRESOLVED",
        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
        to_stage=Stage.RECONCILIATION,
        severity="high",
        explanation=explanation,
        evidence_sources=sources,
        operation_id=operation_id,
    )


def _latest_claim(
    events: list[StateEvent],
    operation_id: str | None,
) -> StateEvent | None:
    candidates = [
        event
        for event in events
        if event.stage == Stage.CLAIMED_RESULT
        and event.key in {"operation_status", "outcome_status", "payment_status"}
        and event.operation_id == operation_id
    ]
    return candidates[-1] if candidates else None


def verify_settlement_attribution_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Keep ledger finality separate from operation attribution.

    An adapter may know authoritatively that value moved while lacking an
    authoritative receipt, authorization, provider record, or stable operation
    identifier that binds that movement to a particular business operation.

    Such ledger evidence is normalized as ``candidate_payment_status`` with
    ``authoritative=True`` and a ``payment_id``, but without an ``operation_id``.
    The attempted operation separately carries a
    ``settlement_operation_binding`` reconciliation event whose value includes
    ``status``, ``payment_id``, and optionally ``confidence``.

    A binding is closed only by an authoritative binding event with a bound
    status. Contextual correlation may identify a strong candidate, but it must
    not promote the transaction into operation-level truth.
    """

    materialized = list(events)
    candidate_settlements = [
        event
        for event in materialized
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key in {"candidate_payment_status", "unbound_payment_status"}
        and event.authoritative
        and _status(event.value) in _SETTLED_STATUSES
        and event.payment_id
        and event.operation_id is None
    ]

    findings: list[Finding] = []
    for binding in materialized:
        if binding.stage != Stage.RECONCILIATION:
            continue
        if binding.key != "settlement_operation_binding":
            continue
        if not isinstance(binding.value, Mapping):
            continue

        binding_status = _status(binding.value.get("status"))
        confidence = _status(binding.value.get("confidence"))
        if binding.authoritative and binding_status in _BOUND_BINDING_STATUSES:
            continue
        if (
            binding_status not in _UNRESOLVED_BINDING_STATUSES
            and confidence not in _UNRESOLVED_BINDING_STATUSES
            and binding_status not in _BOUND_BINDING_STATUSES
        ):
            continue

        payment_id = binding.value.get("payment_id")
        if not isinstance(payment_id, str) or not payment_id:
            continue
        candidate = next(
            (
                event
                for event in candidate_settlements
                if event.payment_id == payment_id
            ),
            None,
        )
        if candidate is None:
            continue

        claim = _latest_claim(materialized, binding.operation_id)
        confidence_text = confidence or binding_status or "unresolved"
        findings.append(
            _finding(
                explanation=(
                    f"Authoritative ledger settlement {payment_id!r} exists, but its "
                    f"binding to operation {binding.operation_id!r} is only "
                    f"{confidence_text!r}. Do not infer operation-level settlement, "
                    "delivery, or reconciliation until an authoritative linkage is supplied."
                ),
                operation_id=binding.operation_id,
                evidence=[claim, candidate, binding],
            )
        )

    return findings
