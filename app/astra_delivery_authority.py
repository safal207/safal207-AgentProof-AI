from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .astra_verifier import Finding, Stage, StateEvent


_IDENTITY_FIELDS = ("authorization_id", "payment_id")
_VERIFIED_STATUSES = {
    "accepted",
    "approved",
    "ok",
    "success",
    "valid",
    "verified",
}
_FAILED_FINALITY_STATUSES = {
    "cancelled",
    "canceled",
    "expired",
    "failed",
    "not_settled",
    "rejected",
    "reverted",
    "voided",
}
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
_ACTIVE_CACHE_STATUSES = {
    "active",
    "available",
    "cached",
    "replayable",
    "verified",
}
_SAFE_CACHE_STATUSES = {
    "absent",
    "consumed",
    "expired",
    "invalidated",
    "revoked",
}
_DELIVERED_STATUSES = {"complete", "completed", "delivered", "success", "succeeded"}
_ENTITLEMENT_STATUSES = {"active", "granted", "valid"}
_REPLAY_MARKERS = {"replay", "replayed", "retry", "same_authorization_replay"}


@dataclass(frozen=True)
class _Contract:
    event: StateEvent
    allow_non_payment_entitlement: bool


def _contract(value: Any) -> tuple[bool, bool, bool]:
    if value is True:
        return True, True, False
    if not isinstance(value, Mapping):
        return False, False, False
    required = value.get("required") is True
    valid = required and value.get("verification_not_delivery_authority") is True
    allow_entitlement = value.get("allow_non_payment_entitlement") is True
    return required, valid, allow_entitlement


def _status(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("status")
    if value is None:
        return None
    return str(value).strip().lower()


def _basis(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("basis")
    return _status(value)


def _scope(
    events: list[tuple[int, StateEvent]],
    operation_id: str | None,
) -> list[tuple[int, StateEvent]]:
    if operation_id is None:
        return [item for item in events if item[1].operation_id is None]
    return [item for item in events if item[1].operation_id == operation_id]


def _has_identity(event: StateEvent | None) -> bool:
    return bool(
        event is not None
        and any(getattr(event, field) is not None for field in _IDENTITY_FIELDS)
    )


def _identity_relation(reference: StateEvent, target: StateEvent) -> str:
    """Compare typed payment identifiers without crossing namespaces."""

    if not _has_identity(reference) or not _has_identity(target):
        return "unresolved"

    shared = False
    for field in _IDENTITY_FIELDS:
        expected = getattr(reference, field)
        observed = getattr(target, field)
        if expected is None or observed is None:
            continue
        shared = True
        if expected != observed:
            return "divergent"
    return "matched" if shared else "unresolved"


def _matches_attempt(reference: StateEvent, target: StateEvent) -> bool:
    relation = _identity_relation(reference, target)
    if relation == "matched":
        return True
    if relation == "divergent":
        return False
    return bool(
        reference.attempt_id is not None
        and target.attempt_id is not None
        and reference.attempt_id == target.attempt_id
    )


def _is_replay_attempt(event: StateEvent) -> bool:
    status = _status(event.value)
    if status in _REPLAY_MARKERS:
        return True
    return bool(status and ("replay" in status or "retry" in status))


def _finding(
    *,
    code: str,
    from_stage: Stage,
    to_stage: Stage,
    severity: str,
    explanation: str,
    operation_id: str | None,
    evidence: Iterable[StateEvent | None],
) -> Finding:
    return Finding(
        code=code,
        from_stage=from_stage,
        to_stage=to_stage,
        severity=severity,
        explanation=explanation,
        evidence_sources=tuple(
            dict.fromkeys(
                event.source
                for event in evidence
                if event is not None and event.source
            )
        ),
        operation_id=operation_id,
    )


def _append_unique(
    findings: list[Finding],
    seen: set[tuple[str, str | None]],
    finding: Finding,
) -> None:
    key = (finding.code, finding.operation_id)
    if key in seen:
        return
    seen.add(key)
    findings.append(finding)


def _latest_matching(
    events: list[tuple[int, StateEvent]],
    *,
    after: int,
    before_or_at: int | None = None,
    stage: Stage,
    key: str,
    reference: StateEvent,
    authoritative: bool | None = None,
) -> tuple[int, StateEvent] | None:
    matched: list[tuple[int, StateEvent]] = []
    for index, event in events:
        if index <= after or (before_or_at is not None and index > before_or_at):
            continue
        if event.stage != stage or event.key != key:
            continue
        if authoritative is not None and event.authoritative is not authoritative:
            continue
        if _identity_relation(reference, event) != "matched":
            continue
        matched.append((index, event))
    return matched[-1] if matched else None


def _valid_non_payment_entitlement(
    events: list[tuple[int, StateEvent]],
    *,
    after: int,
    before_or_at: int,
    operation_id: str | None,
) -> StateEvent | None:
    candidates = [
        event
        for index, event in events
        if after < index <= before_or_at
        and event.operation_id == operation_id
        and event.stage in {Stage.POLICY_DECISION, Stage.RECONCILIATION}
        and event.key == "non_payment_entitlement_status"
        and event.authoritative
        and _status(event.value) in _ENTITLEMENT_STATUSES
    ]
    return candidates[-1] if candidates else None


def verify_finality_bound_delivery_authority(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Ensure verification state cannot become delivery authority after failure.

    The verifier distinguishes admission verification, settlement finality,
    verification-derived cache state, response provenance, and delivery. A
    valid response hash is intentionally ignored as payment evidence.

    Replay delivery is claimed only when the same typed payment identity is
    observed after authoritative settlement failure and no later settlement or
    independently authorized non-payment entitlement supports the delivery.
    """

    indexed = list(enumerate(events))
    findings: list[Finding] = []
    seen: set[tuple[str, str | None]] = set()

    declarations: dict[str | None, _Contract] = {}
    for _, event in indexed:
        if event.stage not in {
            Stage.QUOTE_CHALLENGE,
            Stage.MANDATE_AUTHORIZATION,
            Stage.POLICY_DECISION,
        }:
            continue
        if event.key != "requires_finality_bound_delivery_authority":
            continue
        required, valid, allow_entitlement = _contract(event.value)
        if not required:
            continue
        if not valid:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="FINALITY_BOUND_DELIVERY_CONTRACT_INVALID",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                    severity="medium",
                    explanation=(
                        "The delivery-authority contract does not explicitly "
                        "state that verification is not delivery authority."
                    ),
                    operation_id=event.operation_id,
                    evidence=[event],
                ),
            )
            continue
        declarations[event.operation_id] = _Contract(event, allow_entitlement)

    for operation_id, contract in declarations.items():
        scoped = _scope(indexed, operation_id)
        verifications = [
            (index, event)
            for index, event in scoped
            if event.stage == Stage.CLAIMED_RESULT
            and event.key == "payment_verification_status"
            and _status(event.value) in _VERIFIED_STATUSES
        ]

        if not verifications:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="PAYMENT_VERIFICATION_EVIDENCE_MISSING",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.CLAIMED_RESULT,
                    severity="medium",
                    explanation=(
                        "Finality-bound delivery verification is required, but "
                        "the trace contains no successful payment-verification event."
                    ),
                    operation_id=operation_id,
                    evidence=[contract.event],
                ),
            )
            continue

        for verification_index, verification in verifications:
            finalities = [
                (index, event)
                for index, event in scoped
                if index > verification_index
                and event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
                and event.key == "payment_status"
                and event.authoritative
                and _identity_relation(verification, event) == "matched"
            ]
            if not finalities:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SETTLEMENT_FINALITY_EVIDENCE_MISSING",
                        from_stage=Stage.CLAIMED_RESULT,
                        to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        severity="medium",
                        explanation=(
                            "Payment verification succeeded, but no authoritative "
                            "finality event can be bound to the same payment identity."
                        ),
                        operation_id=operation_id,
                        evidence=[contract.event, verification],
                    ),
                )
                continue

            finality_index, finality = finalities[-1]
            finality_status = _status(finality.value)
            if finality_status in _SETTLED_STATUSES:
                continue
            if finality_status not in _FAILED_FINALITY_STATUSES:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SETTLEMENT_FINALITY_EVIDENCE_MISSING",
                        from_stage=Stage.CLAIMED_RESULT,
                        to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        severity="medium",
                        explanation=(
                            f"Finality remains {finality.value!r}; terminal "
                            "settlement authority is unresolved."
                        ),
                        operation_id=operation_id,
                        evidence=[contract.event, verification, finality],
                    ),
                )
                continue

            cache = _latest_matching(
                scoped,
                after=finality_index,
                stage=Stage.RECONCILIATION,
                key="admission_cache_status",
                reference=verification,
            )
            if cache is None:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="VERIFICATION_CACHE_STATUS_MISSING",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECONCILIATION,
                        severity="medium",
                        explanation=(
                            "Settlement failed after verification, but the trace "
                            "does not show whether verification-derived admission "
                            "state was revoked."
                        ),
                        operation_id=operation_id,
                        evidence=[contract.event, verification, finality],
                    ),
                )
            elif _status(cache[1].value) in _ACTIVE_CACHE_STATUSES:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="VERIFICATION_CACHE_SURVIVES_SETTLEMENT_FAILURE",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECONCILIATION,
                        severity="high",
                        explanation=(
                            "Verification-derived admission state remains active "
                            "after authoritative settlement failure."
                        ),
                        operation_id=operation_id,
                        evidence=[contract.event, verification, finality, cache[1]],
                    ),
                )

            later_attempts = [
                (index, event)
                for index, event in scoped
                if index > finality_index
                and event.stage == Stage.PAYMENT_ATTEMPT
                and event.key == "attempt"
            ]
            replays: list[tuple[int, StateEvent]] = []
            unresolved_replays: list[StateEvent] = []
            for attempt_index, attempt in later_attempts:
                relation = _identity_relation(verification, attempt)
                if relation == "matched":
                    replays.append((attempt_index, attempt))
                elif relation == "unresolved" and _is_replay_attempt(attempt):
                    unresolved_replays.append(attempt)

            if unresolved_replays:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="REPLAY_PAYMENT_IDENTITY_UNRESOLVED",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.PAYMENT_ATTEMPT,
                        severity="medium",
                        explanation=(
                            "A replay-like attempt follows settlement failure, but "
                            "the trace cannot prove whether it reused the failed "
                            "payment identity."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            contract.event,
                            verification,
                            finality,
                            *unresolved_replays,
                        ],
                    ),
                )

            for replay_index, replay in replays:
                deliveries = [
                    (index, event)
                    for index, event in scoped
                    if index > replay_index
                    and event.stage == Stage.RESOURCE_OUTCOME_DELIVERY
                    and event.key == "delivery_status"
                    and _status(event.value) in _DELIVERED_STATUSES
                    and _matches_attempt(replay, event)
                ]
                if not deliveries:
                    continue

                delivery_index, delivery = deliveries[0]
                basis = _latest_matching(
                    scoped,
                    after=replay_index,
                    before_or_at=delivery_index,
                    stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                    key="delivery_authority_basis",
                    reference=replay,
                )
                settled_after_replay = _latest_matching(
                    scoped,
                    after=replay_index,
                    before_or_at=delivery_index,
                    stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    key="payment_status",
                    reference=replay,
                    authoritative=True,
                )
                has_settlement_authority = bool(
                    settled_after_replay
                    and _status(settled_after_replay[1].value) in _SETTLED_STATUSES
                )

                entitlement = None
                if contract.allow_non_payment_entitlement:
                    entitlement = _valid_non_payment_entitlement(
                        scoped,
                        after=replay_index,
                        before_or_at=delivery_index,
                        operation_id=operation_id,
                    )
                basis_value = _basis(basis[1].value) if basis else None
                has_entitlement_authority = bool(
                    basis_value == "non_payment_entitlement" and entitlement
                )

                if basis is None:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="DELIVERY_AUTHORITY_BASIS_MISSING",
                            from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="medium",
                            explanation=(
                                "A replay produced delivery, but the trace does not "
                                "identify the authority used to release the outcome."
                            ),
                            operation_id=operation_id,
                            evidence=[contract.event, verification, finality, replay, delivery],
                        ),
                    )
                elif basis_value == "verification_cache":
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="VERIFICATION_USED_AS_DELIVERY_AUTHORITY",
                            from_stage=Stage.CLAIMED_RESULT,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="high",
                            explanation=(
                                "The delivered outcome names verification-derived "
                                "cache state, rather than finality, as its authority."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                contract.event,
                                verification,
                                finality,
                                replay,
                                basis[1],
                                delivery,
                            ],
                        ),
                    )
                elif basis_value == "settlement_finality" and not has_settlement_authority:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="DELIVERY_AUTHORITY_FINALITY_UNRESOLVED",
                            from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="high",
                            explanation=(
                                "Delivery claims settlement finality as authority, "
                                "but no matching settled event precedes delivery."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                contract.event,
                                verification,
                                finality,
                                replay,
                                basis[1],
                                delivery,
                            ],
                        ),
                    )
                elif basis_value == "non_payment_entitlement" and not has_entitlement_authority:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="DELIVERY_AUTHORITY_FINALITY_UNRESOLVED",
                            from_stage=Stage.POLICY_DECISION,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="high",
                            explanation=(
                                "Delivery claims a non-payment entitlement, but no "
                                "authoritative entitlement evidence supports it."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                contract.event,
                                verification,
                                finality,
                                replay,
                                basis[1],
                                delivery,
                            ],
                        ),
                    )

                if not has_settlement_authority and not has_entitlement_authority:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT",
                            from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="critical",
                            explanation=(
                                "The same payment identity was replayed into a "
                                "delivered outcome after authoritative settlement "
                                "failure, without later settlement or separate "
                                "entitlement authority."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                contract.event,
                                verification,
                                finality,
                                replay,
                                basis[1] if basis else None,
                                delivery,
                            ],
                        ),
                    )

    return findings


__all__ = ["verify_finality_bound_delivery_authority"]
