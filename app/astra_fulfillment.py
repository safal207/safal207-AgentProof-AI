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

_FAILED_FULFILLMENT_STATUSES = {
    "declined",
    "error",
    "failed",
    "issuer_failed",
    "rejected",
}

_ISSUED_STATUSES = {
    "created",
    "issued",
    "provisioned",
    "success",
    "succeeded",
}

_CLIENT_UNOBSERVED_STATUSES = {
    "client_disconnected",
    "missing",
    "not_observed",
    "response_lost",
    "unknown",
    "unobserved",
}

_BOUND_STATUSES = {
    "authoritative",
    "bound",
    "verified",
}

_UNRESOLVED_STATUSES = {
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


def _latest(
    events: list[StateEvent],
    *,
    stage: Stage,
    keys: set[str],
    authoritative: bool | None = None,
) -> StateEvent | None:
    matches = [
        event
        for event in events
        if event.stage == stage
        and event.key in keys
        and (authoritative is None or event.authoritative is authoritative)
    ]
    return matches[-1] if matches else None


def _finding(
    *,
    code: str,
    explanation: str,
    severity: str,
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
        code=code,
        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
        to_stage=(
            Stage.RECONCILIATION
            if code == "REFUND_OPERATION_BINDING_UNRESOLVED"
            else Stage.RESOURCE_OUTCOME_DELIVERY
        ),
        severity=severity,
        explanation=explanation,
        evidence_sources=sources,
        operation_id=operation_id,
    )


def _binding_disposition(event: StateEvent) -> tuple[str | None, str | None]:
    if not isinstance(event.value, Mapping):
        return None, None
    return _status(event.value.get("status")), _status(event.value.get("confidence"))


def _is_recognized_binding(event: StateEvent) -> bool:
    status, confidence = _binding_disposition(event)
    return bool(
        status in _BOUND_STATUSES
        or status in _UNRESOLVED_STATUSES
        or confidence in _UNRESOLVED_STATUSES
    )


def _latest_effective_binding(group: list[StateEvent]) -> StateEvent:
    """Prefer the latest authoritative reconciliation event when one exists.

    A later untrusted marker must not overwrite an authoritative provider
    reconciliation record. When several authoritative records exist, trace
    order still decides which authoritative state is current. Callers provide
    only recognized binding states, so malformed records cannot suppress a
    previously meaningful unresolved or bound state.
    """

    authoritative = [event for event in group if event.authoritative]
    return authoritative[-1] if authoritative else group[-1]


def verify_provider_fulfillment_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify provider fulfillment after operation-bound payment finality.

    This verifier requires explicit evidence. A provider or issuer outcome only
    becomes authoritative when the adapter marks the corresponding event with
    ``authoritative=True``. Missing delivery evidence by itself never proves an
    issuer failure or a lost response.

    Expected keys:

    - operation-bound authoritative ``payment_status=settled``;
    - authoritative ``fulfillment_status`` / ``issuer_status`` /
      ``provider_outcome`` for the same operation;
    - explicit ``client_delivery_status`` / ``delivery_status`` when the client
      did not observe an issued result;
    - unbound authoritative ``candidate_refund_status`` plus a
      ``refund_operation_binding`` reconciliation event for refund attribution.
    """

    materialized = list(events)
    findings: list[Finding] = []

    settlements_by_operation: dict[str, StateEvent] = {}
    for event in materialized:
        if (
            event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
            and event.key == "payment_status"
            and event.authoritative
            and _status(event.value) in _SETTLED_STATUSES
            and event.operation_id is not None
        ):
            settlements_by_operation[event.operation_id] = event

    for operation_id, settlement in settlements_by_operation.items():
        scoped = [
            event
            for event in materialized
            if event.operation_id == operation_id
        ]

        # Provider truth is selected only from authoritative provider events.
        # A later untrusted callback cannot mask an earlier authoritative state.
        fulfillment = _latest(
            scoped,
            stage=Stage.RESOURCE_OUTCOME_DELIVERY,
            keys={
                "fulfillment_status",
                "issuer_status",
                "merchant_outcome",
                "provider_outcome",
            },
            authoritative=True,
        )
        fulfillment_status = _status(fulfillment.value) if fulfillment else None

        if fulfillment_status in _FAILED_FULFILLMENT_STATUSES:
            findings.append(
                _finding(
                    code="SETTLED_FULFILLMENT_FAILED",
                    severity="critical",
                    explanation=(
                        "Authoritative payment finality exists, but the "
                        f"provider fulfillment outcome is {fulfillment.value!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[settlement, fulfillment],
                )
            )

        delivery = _latest(
            scoped,
            stage=Stage.RESOURCE_OUTCOME_DELIVERY,
            keys={"client_delivery_status", "delivery_status"},
        )
        delivery_status = _status(delivery.value) if delivery else None
        if (
            fulfillment_status in _ISSUED_STATUSES
            and delivery
            and delivery_status in _CLIENT_UNOBSERVED_STATUSES
        ):
            findings.append(
                _finding(
                    code="ISSUED_BUT_CLIENT_UNOBSERVED",
                    severity="high",
                    explanation=(
                        "The provider authoritatively reports an issued result, "
                        f"but the explicit client delivery state is {delivery.value!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[settlement, fulfillment, delivery],
                )
            )

    candidate_refunds_by_payment: dict[str, StateEvent] = {}
    for event in materialized:
        if (
            event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
            and event.key in {"candidate_refund_status", "unbound_refund_status"}
            and event.authoritative
            and _status(event.value) in _SETTLED_STATUSES | {"refunded"}
            and event.payment_id
            and event.operation_id is None
        ):
            candidate_refunds_by_payment[event.payment_id] = event

    binding_groups: dict[tuple[str | None, str], list[StateEvent]] = {}
    for binding in materialized:
        if binding.stage != Stage.RECONCILIATION:
            continue
        if binding.key != "refund_operation_binding":
            continue
        if not isinstance(binding.value, Mapping):
            continue
        if not _is_recognized_binding(binding):
            continue

        payment_id = binding.value.get("payment_id")
        if not isinstance(payment_id, str) or not payment_id:
            continue
        if payment_id not in candidate_refunds_by_payment:
            continue

        binding_groups.setdefault(
            (binding.operation_id, payment_id),
            [],
        ).append(binding)

    for (operation_id, payment_id), group in binding_groups.items():
        binding = _latest_effective_binding(group)
        binding_status, confidence = _binding_disposition(binding)
        binding_is_terminal = bool(
            binding.authoritative
            and bool(operation_id)
            and binding_status in _BOUND_STATUSES
            and confidence not in _UNRESOLVED_STATUSES
        )
        if binding_is_terminal:
            continue

        refund = candidate_refunds_by_payment[payment_id]
        confidence_text = confidence or binding_status or "unresolved"
        findings.append(
            _finding(
                code="REFUND_OPERATION_BINDING_UNRESOLVED",
                severity="high",
                explanation=(
                    f"Authoritative refund movement {payment_id!r} exists, but "
                    f"its binding to operation {operation_id!r} is only "
                    f"{confidence_text!r}. Do not treat it as terminal recovery "
                    "until an authoritative linkage is supplied."
                ),
                operation_id=operation_id,
                evidence=[refund, binding],
            )
        )

    return findings
