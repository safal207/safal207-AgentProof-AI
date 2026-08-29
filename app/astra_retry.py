from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .astra_verifier import Finding, Stage, StateEvent


_INDETERMINATE_STATUSES = {
    "connection_lost",
    "indeterminate",
    "network_error",
    "pending_reconciliation",
    "settlement_unavailable",
    "timed_out",
    "timeout",
    "unavailable",
    "unknown",
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

_NOT_SETTLED_STATUSES = {
    "cancelled",
    "canceled",
    "expired",
    "failed",
    "not_settled",
    "rejected",
    "reversed",
    "voided",
}


def _status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _contract(value: Any) -> tuple[bool, bool]:
    if value is True:
        return True, False
    if not isinstance(value, Mapping):
        return False, False
    return (
        value.get("required") is True,
        value.get("same_authorization_idempotent") is True,
    )


def _scope(events: list[StateEvent], operation_id: str | None) -> list[StateEvent]:
    if operation_id is None:
        return [event for event in events if event.operation_id is None]
    return [event for event in events if event.operation_id == operation_id]


def _matches_attempt(event: StateEvent, attempt: StateEvent) -> bool:
    compared = False
    for field in ("payment_id", "attempt_id", "authorization_id"):
        event_value = getattr(event, field)
        attempt_value = getattr(attempt, field)
        if event_value is None or attempt_value is None:
            continue
        compared = True
        if event_value != attempt_value:
            return False
    if compared:
        return True
    return event.operation_id == attempt.operation_id


def _fresh_identity(previous: StateEvent, retry: StateEvent) -> bool | None:
    comparisons: list[bool] = []
    if previous.authorization_id is not None and retry.authorization_id is not None:
        comparisons.append(previous.authorization_id != retry.authorization_id)
    if previous.payment_id is not None and retry.payment_id is not None:
        comparisons.append(previous.payment_id != retry.payment_id)
    return any(comparisons) if comparisons else None


def _finding(
    *,
    code: str,
    severity: str,
    explanation: str,
    operation_id: str | None,
    evidence: Iterable[StateEvent | None],
) -> Finding:
    return Finding(
        code=code,
        from_stage=Stage.CLAIMED_RESULT,
        to_stage=Stage.PAYMENT_ATTEMPT,
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


def verify_indeterminate_retry_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify retry authorization identity after an indeterminate settlement result.

    Protocol adapters opt in with a
    ``requires_resolution_before_fresh_authorization_after_indeterminate``
    event at QUOTE/CHALLENGE or MANDATE/AUTHORIZATION. The optional contract
    field ``same_authorization_idempotent`` declares that resubmitting the same
    payment authorization cannot create another economic charge on the rail.

    This verifier detects an unsafe retry contract. It does not claim that a
    duplicate payment occurred unless separate authoritative settlement events
    establish that stronger fact through the base verifier.
    """

    materialized = list(events)
    declarations: dict[str | None, tuple[StateEvent, bool]] = {}
    for event in materialized:
        if event.stage not in {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION}:
            continue
        if (
            event.key
            != "requires_resolution_before_fresh_authorization_after_indeterminate"
        ):
            continue
        required, same_authorization_idempotent = _contract(event.value)
        if required:
            declarations[event.operation_id] = (
                event,
                same_authorization_idempotent,
            )

    findings: list[Finding] = []
    for operation_id, (declaration, same_authorization_idempotent) in declarations.items():
        scoped = _scope(materialized, operation_id)
        indexed_attempts = [
            (index, event)
            for index, event in enumerate(scoped)
            if event.stage == Stage.PAYMENT_ATTEMPT and event.key == "attempt"
        ]

        for (previous_index, previous), (retry_index, retry) in zip(
            indexed_attempts,
            indexed_attempts[1:],
        ):
            between = scoped[previous_index + 1 : retry_index]
            relevant_claims = [
                event
                for event in between
                if event.stage == Stage.CLAIMED_RESULT
                and event.key == "payment_status"
                and _matches_attempt(event, previous)
            ]
            relevant_finality = [
                event
                for event in between
                if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
                and event.key == "payment_status"
                and event.authoritative
                and _matches_attempt(event, previous)
            ]

            last_claim = relevant_claims[-1] if relevant_claims else None
            last_finality = relevant_finality[-1] if relevant_finality else None
            claim_status = _status(last_claim.value) if last_claim else None
            finality_status = _status(last_finality.value) if last_finality else None
            indeterminate = (
                claim_status in _INDETERMINATE_STATUSES
                or finality_status in _INDETERMINATE_STATUSES
            )
            if not indeterminate:
                continue

            fresh_identity = _fresh_identity(previous, retry)
            if fresh_identity is None:
                findings.append(
                    _finding(
                        code="RETRY_PAYMENT_IDENTITY_UNRESOLVED",
                        severity="medium",
                        explanation=(
                            "A retry followed an indeterminate settlement result, but the "
                            "trace does not expose comparable authorization_id or payment_id "
                            "values for the original and retry attempts."
                        ),
                        operation_id=operation_id,
                        evidence=[declaration, previous, last_claim, last_finality, retry],
                    )
                )
                continue

            if not fresh_identity:
                if finality_status in _SETTLED_STATUSES | _NOT_SETTLED_STATUSES:
                    continue
                if same_authorization_idempotent:
                    continue
                findings.append(
                    _finding(
                        code="INDETERMINATE_RETRY_IDEMPOTENCY_UNPROVEN",
                        severity="medium",
                        explanation=(
                            "The retry reused the same payment identity after an "
                            "indeterminate result, but the protocol contract does not "
                            "establish idempotent settlement for that identity."
                        ),
                        operation_id=operation_id,
                        evidence=[declaration, previous, last_claim, last_finality, retry],
                    )
                )
                continue

            if finality_status in _NOT_SETTLED_STATUSES:
                continue

            if finality_status in _SETTLED_STATUSES:
                findings.append(
                    _finding(
                        code="FRESH_AUTHORIZATION_AFTER_CONFIRMED_SETTLEMENT",
                        severity="critical",
                        explanation=(
                            "A fresh payment authorization was created for the same "
                            "business operation after authoritative evidence had already "
                            "confirmed settlement of the previous payment."
                        ),
                        operation_id=operation_id,
                        evidence=[declaration, previous, last_claim, last_finality, retry],
                    )
                )
                continue

            findings.append(
                _finding(
                    code="FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT",
                    severity="high",
                    explanation=(
                        "A fresh independently spendable authorization was created for "
                        "the same business operation before the previous payment's "
                        "settlement state was authoritatively resolved. This is an unsafe "
                        "retry contract, not proof that duplicate settlement occurred."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, previous, last_claim, last_finality, retry],
                )
            )

    return findings


__all__ = ["verify_indeterminate_retry_outcome"]
