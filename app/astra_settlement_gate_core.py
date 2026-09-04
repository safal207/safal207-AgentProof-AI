from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
_FAILED_STATUSES = {
    "cancelled",
    "canceled",
    "expired",
    "failed",
    "not_settled",
    "rejected",
    "reverted",
    "voided",
}
_PRIVATE_RESPONSE_STATES = {
    "buffered",
    "generated",
    "held",
    "private",
    "staged",
}
_PUBLIC_RESPONSE_STATES = {
    "client_visible",
    "committed",
    "flushed",
    "published",
}
_DISCARDED_RESPONSE_STATES = {
    "absent",
    "discarded",
    "invalidated",
    "revoked",
}
_RETAINED_RESPONSE_STATES = {
    "active",
    "buffered",
    "generated",
    "held",
    "retained",
    "staged",
}
_DELIVERED_STATUSES = {
    "complete",
    "completed",
    "delivered",
    "success",
    "succeeded",
}


@dataclass(frozen=True)
class _IndexedEvent:
    index: int
    event: StateEvent


@dataclass(frozen=True)
class _Contract:
    events: tuple[StateEvent, ...]
    provenance_required: bool


@dataclass(frozen=True)
class _Attempt:
    verification: _IndexedEvent
    identity: Mapping[str, str]


@dataclass(frozen=True)
class _AttemptEvents:
    response_states: tuple[_IndexedEvent, ...]
    public_events: tuple[tuple[_IndexedEvent, str], ...]
    finalities: tuple[_IndexedEvent, ...]
    divergent: tuple[_IndexedEvent, ...]
    transport_only: tuple[_IndexedEvent, ...]


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        normalized = str(value).strip()
        return normalized or None
    return None


def _status(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("status", value.get("state"))
    text = _text(value)
    return text.lower() if text is not None else None


def _epoch(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return Decimal(str(value))
    if isinstance(value, str):
        stripped = value.strip()
        try:
            numeric = Decimal(stripped)
        except (InvalidOperation, ValueError):
            try:
                parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return None
            return Decimal(str(parsed.timestamp()))
        return numeric if numeric.is_finite() else None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return Decimal(str(value.timestamp()))
    return None


def _contract(value: Any) -> tuple[bool, bool, bool]:
    if not isinstance(value, Mapping):
        return False, False, False
    required = value.get("required") is True
    valid = bool(
        required
        and value.get("protected_body_must_remain_discardable_until_settled")
        is True
    )
    provenance_required = value.get("implementation_provenance_required") is True
    return required, valid, provenance_required


def _canonical_finality(value: Any) -> str | None:
    status = _status(value)
    if status in _SETTLED_STATUSES:
        return "settled"
    if status in _FAILED_STATUSES:
        return "failed"
    return None


def _identity_values(event: StateEvent) -> dict[str, str]:
    return {
        field: value
        for field in _IDENTITY_FIELDS
        if (value := getattr(event, field)) is not None
    }


def _same_attempt_id(attempt: _Attempt, event: StateEvent) -> bool:
    return bool(
        attempt.verification.event.attempt_id is not None
        and event.attempt_id is not None
        and attempt.verification.event.attempt_id == event.attempt_id
    )


def _event_relation(attempt: _Attempt, event: StateEvent) -> str:
    """Classify evidence without confusing a fresh payment with a conflict.

    A different typed payment identity under a different attempt is unrelated,
    even when it belongs to the same business operation. A conflict requires
    either a partial typed-identity match plus a disagreement, or a reused
    transport attempt ID carrying a different typed identity.
    """

    event_identity = _identity_values(event)
    if not attempt.identity:
        return "transport_only" if _same_attempt_id(attempt, event) else "unrelated"
    if not event_identity:
        return "transport_only" if _same_attempt_id(attempt, event) else "unrelated"

    shared = set(attempt.identity) & set(event_identity)
    equal_shared = {
        field
        for field in shared
        if attempt.identity[field] == event_identity[field]
    }
    conflicting_shared = shared - equal_shared

    if conflicting_shared:
        if equal_shared or _same_attempt_id(attempt, event):
            return "divergent"
        return "unrelated"
    if equal_shared:
        return "matched"
    return "transport_only" if _same_attempt_id(attempt, event) else "unrelated"


def _no_later_than(left: _IndexedEvent, right: _IndexedEvent) -> bool:
    left_time = _epoch(left.event.observed_at)
    right_time = _epoch(right.event.observed_at)
    if left_time is not None and right_time is not None:
        return left_time <= right_time
    return left.index <= right.index


def _strictly_later(left: _IndexedEvent, right: _IndexedEvent) -> bool:
    return not _no_later_than(left, right)


def _chronological(items: Iterable[_IndexedEvent]) -> list[_IndexedEvent]:
    materialized = list(items)
    if not materialized:
        return []
    times = [_epoch(item.event.observed_at) for item in materialized]
    if all(value is not None for value in times):
        return [
            item
            for _, item in sorted(
                zip(times, materialized, strict=True),
                key=lambda pair: (pair[0], pair[1].index),
            )
        ]
    return sorted(materialized, key=lambda item: item.index)


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


def _applicable_contract(
    contracts: Mapping[str | None, _Contract],
    operation_id: str | None,
) -> _Contract | None:
    specific = contracts.get(operation_id)
    global_contract = contracts.get(None)
    if specific is None:
        return global_contract
    if global_contract is None or operation_id is None:
        return specific
    return _Contract(
        events=(*global_contract.events, *specific.events),
        provenance_required=(
            global_contract.provenance_required
            or specific.provenance_required
        ),
    )


def _valid_provenance(event: StateEvent) -> bool:
    if not event.authoritative or not isinstance(event.value, Mapping):
        return False
    language = _text(event.value.get("language"))
    artifact = _text(event.value.get("artifact"))
    revision = _text(event.value.get("commit")) or _text(
        event.value.get("revision")
    )
    version = _text(event.value.get("version"))
    return bool(language and artifact and (revision or version))


def _public_kind(item: _IndexedEvent) -> str | None:
    event = item.event
    if event.stage != Stage.RESOURCE_OUTCOME_DELIVERY:
        return None
    if event.key == "protected_response_state":
        return "commit" if _status(event.value) in _PUBLIC_RESPONSE_STATES else None
    if event.key == "delivery_status" and _status(event.value) in _DELIVERED_STATUSES:
        return "delivery"
    return None


def _collect_attempt_events(
    scoped: Iterable[_IndexedEvent],
    attempt: _Attempt,
) -> _AttemptEvents:
    response_states: list[_IndexedEvent] = []
    public_events: list[tuple[_IndexedEvent, str]] = []
    finalities: list[_IndexedEvent] = []
    divergent: list[_IndexedEvent] = []
    transport_only: list[_IndexedEvent] = []

    for item in scoped:
        event = item.event
        relevant = bool(
            (
                event.stage == Stage.RESOURCE_OUTCOME_DELIVERY
                and event.key in {"protected_response_state", "delivery_status"}
            )
            or (
                event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
                and event.key == "payment_status"
                and event.authoritative
            )
        )
        if not relevant:
            continue

        relation = _event_relation(attempt, event)
        if relation == "unrelated":
            continue
        if relation == "divergent":
            divergent.append(item)
            continue
        if relation == "transport_only":
            transport_only.append(item)

        if (
            event.stage == Stage.RESOURCE_OUTCOME_DELIVERY
            and event.key == "protected_response_state"
        ):
            response_states.append(item)
        public_kind = _public_kind(item)
        if public_kind is not None:
            public_events.append((item, public_kind))
        if (
            event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
            and _canonical_finality(event.value) is not None
        ):
            finalities.append(item)

    return _AttemptEvents(
        response_states=tuple(response_states),
        public_events=tuple(public_events),
        finalities=tuple(finalities),
        divergent=tuple(divergent),
        transport_only=tuple(transport_only),
    )


def _has_public_event_by(
    public_events: Iterable[tuple[_IndexedEvent, str]],
    boundary: _IndexedEvent,
) -> bool:
    return any(_no_later_than(item, boundary) for item, _ in public_events)


def _first_public_after(
    public_events: Iterable[tuple[_IndexedEvent, str]],
    boundary: _IndexedEvent,
) -> _IndexedEvent | None:
    candidates = [
        item for item, _ in public_events if _strictly_later(item, boundary)
    ]
    ordered = _chronological(candidates)
    return ordered[0] if ordered else None


def verify_settlement_gated_delivery(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify that protected output remains private until settlement succeeds.

    Handler execution and private response generation may precede settlement.
    Public commit, flush, or protected delivery may not. Build/version metadata
    identifies the artifact under test but never proves behavioral conformance.
    """

    indexed = [
        _IndexedEvent(index, event)
        for index, event in enumerate(events)
    ]
    findings: list[Finding] = []
    seen: set[tuple[str, str | None]] = set()

    contract_groups: dict[str | None, list[tuple[StateEvent, bool]]] = {}
    invalid_contracts: set[str | None] = set()
    for item in indexed:
        event = item.event
        if event.stage not in {
            Stage.QUOTE_CHALLENGE,
            Stage.MANDATE_AUTHORIZATION,
            Stage.POLICY_DECISION,
        }:
            continue
        if event.key != "requires_settlement_gated_delivery":
            continue
        required, valid, provenance_required = _contract(event.value)
        if not required:
            continue
        if not valid:
            invalid_contracts.add(event.operation_id)
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SETTLEMENT_GATED_DELIVERY_CONTRACT_INVALID",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                    severity="medium",
                    explanation=(
                        "The contract does not explicitly require protected output "
                        "to remain discardable until settlement succeeds."
                    ),
                    operation_id=event.operation_id,
                    evidence=[event],
                ),
            )
            continue
        contract_groups.setdefault(event.operation_id, []).append(
            (event, provenance_required)
        )

    if None in invalid_contracts:
        return findings

    contracts = {
        operation_id: _Contract(
            events=tuple(event for event, _ in group),
            provenance_required=any(required for _, required in group),
        )
        for operation_id, group in contract_groups.items()
        if operation_id not in invalid_contracts
    }
    if not contracts:
        return findings

    verifications = [
        item
        for item in indexed
        if item.event.stage == Stage.CLAIMED_RESULT
        and item.event.key == "payment_verification_status"
        and _status(item.event.value) in _VERIFIED_STATUSES
    ]
    operations = set(contracts)
    operations.update(
        item.event.operation_id
        for item in verifications
        if _applicable_contract(contracts, item.event.operation_id) is not None
    )

    for operation_id in sorted(
        operations,
        key=lambda value: "" if value is None else value,
    ):
        if operation_id in invalid_contracts:
            continue
        contract = _applicable_contract(contracts, operation_id)
        if contract is None:
            continue
        scoped = [
            item for item in indexed if item.event.operation_id == operation_id
        ]

        if contract.provenance_required:
            provenance = [
                item.event
                for item in scoped
                if item.event.stage == Stage.RECONCILIATION
                and item.event.key == "implementation_artifact"
                and _valid_provenance(item.event)
            ]
            if not provenance:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SETTLEMENT_GATE_IMPLEMENTATION_PROVENANCE_MISSING",
                        from_stage=Stage.POLICY_DECISION,
                        to_stage=Stage.RECONCILIATION,
                        severity="medium",
                        explanation=(
                            "The trace does not identify an authoritative "
                            "language/artifact plus revision or version for the "
                            "implementation under test."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events],
                    ),
                )

        operation_verifications = [
            item for item in verifications if item.event.operation_id == operation_id
        ]
        if not operation_verifications:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="PAYMENT_VERIFICATION_EVIDENCE_MISSING",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.CLAIMED_RESULT,
                    severity="medium",
                    explanation=(
                        "Settlement-gated delivery is required, but no successful "
                        "payment-verification event appears in the trace."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events],
                ),
            )
            continue

        for verification in operation_verifications:
            attempt = _Attempt(
                verification=verification,
                identity=_identity_values(verification.event),
            )
            if not attempt.identity:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SETTLEMENT_GATE_IDENTITY_UNRESOLVED",
                        from_stage=Stage.CLAIMED_RESULT,
                        to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        severity="medium",
                        explanation=(
                            "Successful verification exposes neither "
                            "authorization_id nor payment_id."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events, verification.event],
                    ),
                )

            related = _collect_attempt_events(scoped, attempt)
            if related.divergent:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SETTLEMENT_GATE_IDENTITY_DIVERGENCE",
                        from_stage=Stage.CLAIMED_RESULT,
                        to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                        severity="high",
                        explanation=(
                            "Evidence reuses part of the verified payment or its "
                            "attempt context while conflicting on another typed "
                            "payment identifier."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            verification.event,
                            *(item.event for item in related.divergent),
                        ],
                    ),
                )
            if related.transport_only:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SETTLEMENT_GATE_IDENTITY_UNRESOLVED",
                        from_stage=Stage.CLAIMED_RESULT,
                        to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        severity="medium",
                        explanation=(
                            "Some gate evidence is correlated only by attempt_id; "
                            "typed payment identity remains unresolved."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            verification.event,
                            *(item.event for item in related.transport_only),
                        ],
                    ),
                )

            if not related.response_states:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="PROTECTED_RESPONSE_GATE_EVIDENCE_MISSING",
                        from_stage=Stage.CLAIMED_RESULT,
                        to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                        severity="medium",
                        explanation=(
                            "The trace does not show whether protected output was "
                            "buffered, committed, flushed, or discarded."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events, verification.event],
                    ),
                )

            ordered_finalities = _chronological(related.finalities)
            ordered_public = sorted(
                related.public_events,
                key=lambda pair: (
                    _epoch(pair[0].event.observed_at)
                    if _epoch(pair[0].event.observed_at) is not None
                    else Decimal(pair[0].index),
                    pair[0].index,
                ),
            )

            for public_event, kind in ordered_public:
                before = [
                    item
                    for item in ordered_finalities
                    if _no_later_than(item, public_event)
                ]
                after = [
                    item
                    for item in ordered_finalities
                    if _strictly_later(item, public_event)
                ]
                finality_before = before[-1] if before else None
                finality_before_status = (
                    _canonical_finality(finality_before.event.value)
                    if finality_before
                    else None
                )

                if finality_before_status == "settled":
                    continue
                if finality_before_status == "failed":
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="PROTECTED_DELIVERY_WITH_FAILED_SETTLEMENT",
                            from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="critical",
                            explanation=(
                                "Protected output became public despite authoritative "
                                "failed settlement for the same payment."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                *contract.events,
                                verification.event,
                                finality_before.event,
                                public_event.event,
                            ],
                        ),
                    )
                    continue

                code = (
                    "PROTECTED_RESPONSE_COMMITTED_BEFORE_SETTLEMENT"
                    if kind == "commit"
                    else "PROTECTED_DELIVERY_PRECEDES_SETTLEMENT"
                )
                explanation = (
                    "The protected response was committed or flushed before "
                    "authoritative settlement succeeded."
                    if kind == "commit"
                    else "Protected resource delivery occurred before "
                    "authoritative settlement success."
                )
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code=code,
                        from_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                        to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        severity="high",
                        explanation=explanation,
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            verification.event,
                            public_event.event,
                        ],
                    ),
                )

                later_terminal = after[0] if after else None
                later_status = (
                    _canonical_finality(later_terminal.event.value)
                    if later_terminal
                    else None
                )
                if later_status == "failed":
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="SETTLEMENT_FAILED_AFTER_PROTECTED_DELIVERY",
                            from_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            severity="critical",
                            explanation=(
                                "Authoritative settlement failed only after the "
                                "protected response had already become public."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                *contract.events,
                                verification.event,
                                public_event.event,
                                later_terminal.event,
                            ],
                        ),
                    )
                elif later_terminal is None:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="PROTECTED_DELIVERY_FINALITY_UNRESOLVED",
                            from_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            severity="medium",
                            explanation=(
                                "Protected output became public without matching "
                                "authoritative terminal settlement evidence."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                *contract.events,
                                verification.event,
                                public_event.event,
                            ],
                        ),
                    )

            failures = [
                item
                for item in ordered_finalities
                if _canonical_finality(item.event.value) == "failed"
            ]
            for failure in failures:
                private_before = [
                    item
                    for item in related.response_states
                    if _status(item.event.value) in _PRIVATE_RESPONSE_STATES
                    and _no_later_than(item, failure)
                ]
                if not private_before:
                    continue

                # Once protected output is public, commit/delivery findings are
                # the relevant result. A disposal finding would only duplicate
                # an already irreversible disclosure.
                if _has_public_event_by(related.public_events, failure):
                    continue

                later_settlement = next(
                    (
                        item
                        for item in ordered_finalities
                        if _strictly_later(item, failure)
                        and _canonical_finality(item.event.value) == "settled"
                    ),
                    None,
                )
                first_public = _first_public_after(related.public_events, failure)
                boundaries = [
                    item
                    for item in (later_settlement, first_public)
                    if item is not None
                ]
                boundary = _chronological(boundaries)[0] if boundaries else None

                # A later settlement before any public reuse makes the staged
                # body economically releasable; explicit disposal is unnecessary.
                if (
                    later_settlement is not None
                    and (
                        first_public is None
                        or _no_later_than(later_settlement, first_public)
                    )
                ):
                    continue

                post_failure_states = [
                    item
                    for item in related.response_states
                    if _strictly_later(item, failure)
                    and (
                        boundary is None
                        or _no_later_than(item, boundary)
                    )
                ]
                latest_post = (
                    _chronological(post_failure_states)[-1]
                    if post_failure_states
                    else None
                )
                post_status = _status(latest_post.event.value) if latest_post else None

                if latest_post is None:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="PROTECTED_BODY_DISPOSAL_EVIDENCE_MISSING",
                            from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="medium",
                            explanation=(
                                "Settlement failed after protected output was staged, "
                                "but disposal or continued non-public state is not "
                                "established before the next release boundary."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                *contract.events,
                                verification.event,
                                private_before[-1].event,
                                failure.event,
                            ],
                        ),
                    )
                elif post_status in _RETAINED_RESPONSE_STATES:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="PROTECTED_BODY_NOT_DISCARDED_AFTER_SETTLEMENT_FAILURE",
                            from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="high",
                            explanation=(
                                "Protected output remained staged or reusable after "
                                "authoritative settlement failure."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                *contract.events,
                                verification.event,
                                failure.event,
                                latest_post.event,
                            ],
                        ),
                    )
                elif post_status not in _DISCARDED_RESPONSE_STATES:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="PROTECTED_BODY_DISPOSAL_EVIDENCE_MISSING",
                            from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                            to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                            severity="medium",
                            explanation=(
                                f"Post-failure response state {latest_post.event.value!r} "
                                "does not establish protected-body disposal."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                *contract.events,
                                verification.event,
                                failure.event,
                                latest_post.event,
                            ],
                        ),
                    )

    return findings


__all__ = ["verify_settlement_gated_delivery"]
