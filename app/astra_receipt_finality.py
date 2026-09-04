from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .astra_verifier import Finding, Stage, StateEvent


_IDENTITY_FIELDS = ("authorization_id", "payment_id")
_RECEIPT_STATUS_KEYS = frozenset({"receipt_status", "payment_status"})
_SUCCESS_STATUSES = {
    "captured",
    "complete",
    "completed",
    "confirmed",
    "paid",
    "settled",
    "success",
    "succeeded",
}
_FAILURE_STATUSES = {
    "cancelled",
    "canceled",
    "error",
    "expired",
    "failed",
    "not_settled",
    "rejected",
    "reverted",
    "voided",
}
_INTEGRITY_VERIFIED = {"authentic", "valid", "verified"}
_INTEGRITY_FAILED = {"failed", "invalid", "tampered", "unverified"}


@dataclass(frozen=True)
class _Contract:
    events: tuple[StateEvent, ...]


@dataclass(frozen=True)
class _IndexedEvent:
    index: int
    event: StateEvent


@dataclass(frozen=True)
class _Confirmation:
    indexed: _IndexedEvent
    psp_confirmation_id: str
    network_confirmation_id: str
    verified_claim: bool | None


@dataclass(frozen=True)
class _Receipt:
    status: _IndexedEvent
    status_value: str
    identity: Mapping[str, str]
    integrity: _IndexedEvent | None
    integrity_verified: bool
    confirmation: _Confirmation | None
    evidence_conflict: bool = False


@dataclass(frozen=True)
class _RailSelection:
    status_before_receipt: _IndexedEvent | None
    status_after_receipt: _IndexedEvent | None
    confirmation_before_receipt: _Confirmation | None
    confirmation_after_receipt: _Confirmation | None
    identity_unresolved: bool = False
    identity_divergent: bool = False
    evidence_conflict: bool = False


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        normalized = str(value).strip()
        return normalized or None
    return None


def _status(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("status")
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


def _canonical_payment_status(value: Any) -> str | None:
    status = _status(value)
    if status in _SUCCESS_STATUSES:
        return "settled"
    if status in _FAILURE_STATUSES:
        return "failed"
    return None


def _contract(value: Any) -> tuple[bool, bool]:
    if not isinstance(value, Mapping):
        return False, False
    required = value.get("required") is True
    valid = bool(
        required
        and value.get("success_requires_settled_finality") is True
        and value.get("verified_flag_is_claim_only") is True
    )
    return required, valid


def _identity_values(event: StateEvent) -> dict[str, str]:
    return {
        field: value
        for field in _IDENTITY_FIELDS
        if (value := getattr(event, field)) is not None
    }


def _identity_profile(
    events: Iterable[StateEvent],
) -> tuple[dict[str, str], bool, bool]:
    """Return typed identity, conflict-free status, and presence status."""

    profile: dict[str, str] = {}
    present = False
    for event in events:
        for field in _IDENTITY_FIELDS:
            value = getattr(event, field)
            if value is None:
                continue
            present = True
            existing = profile.get(field)
            if existing is not None and existing != value:
                return profile, False, True
            profile[field] = value
    return profile, True, present


def _identity_relation(identity: Mapping[str, str], event: StateEvent) -> str:
    if not identity:
        return "unresolved"
    shared = False
    for field, expected in identity.items():
        observed = getattr(event, field)
        if observed is None:
            continue
        shared = True
        if observed != expected:
            return "divergent"
    return "matched" if shared else "unresolved"


def _shares_equal_typed_identity(
    identity: Mapping[str, str],
    event: StateEvent,
) -> bool:
    return any(
        getattr(event, field) is not None
        and getattr(event, field) == expected
        for field, expected in identity.items()
    )


def _belongs_to_receipt(
    event: StateEvent,
    *,
    operation_id: str | None,
    identity: Mapping[str, str],
) -> bool:
    if event.operation_id != operation_id:
        return False
    if identity:
        relation = _identity_relation(identity, event)
        return relation == "matched" or (
            relation == "divergent"
            and _shares_equal_typed_identity(identity, event)
        )
    return not _identity_values(event)


def _confirmation(indexed: _IndexedEvent) -> _Confirmation | None:
    value = indexed.event.value
    if not isinstance(value, Mapping):
        return None
    psp_confirmation_id = _text(value.get("psp_confirmation_id"))
    network_confirmation_id = _text(value.get("network_confirmation_id"))
    verified_claim = value.get("rail_confirmation_verified")
    if verified_claim is not None and not isinstance(verified_claim, bool):
        return None
    if psp_confirmation_id is None or network_confirmation_id is None:
        return None
    return _Confirmation(
        indexed=indexed,
        psp_confirmation_id=psp_confirmation_id,
        network_confirmation_id=network_confirmation_id,
        verified_claim=verified_claim,
    )


def _no_later_than(candidate: _IndexedEvent, receipt: _IndexedEvent) -> bool:
    candidate_time = _epoch(candidate.event.observed_at)
    receipt_time = _epoch(receipt.event.observed_at)
    if candidate_time is not None and receipt_time is not None:
        return candidate_time <= receipt_time
    return candidate.index <= receipt.index


def _chronological(
    candidates: list[_IndexedEvent],
) -> list[_IndexedEvent]:
    if not candidates:
        return []
    times = [_epoch(item.event.observed_at) for item in candidates]
    if all(value is not None for value in times):
        return [
            item
            for _, item in sorted(
                zip(times, candidates, strict=True),
                key=lambda pair: (pair[0], pair[1].index),
            )
        ]
    return sorted(candidates, key=lambda item: item.index)


def _chronological_confirmations(
    candidates: list[_Confirmation],
) -> list[_Confirmation]:
    if not candidates:
        return []
    indexed = _chronological([item.indexed for item in candidates])
    by_index = {item.indexed.index: item for item in candidates}
    return [by_index[item.index] for item in indexed]


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
    return _Contract(events=(*global_contract.events, *specific.events))


def _same_receipt_candidates(
    indexed: list[_IndexedEvent],
    *,
    anchor: _IndexedEvent,
    key: str,
    identity: Mapping[str, str],
    authoritative: bool | None = None,
) -> tuple[list[_IndexedEvent], list[_IndexedEvent]]:
    matched: list[_IndexedEvent] = []
    conflicting: list[_IndexedEvent] = []
    for item in indexed:
        event = item.event
        if event.stage != Stage.RECEIPT or event.key != key:
            continue
        if event.operation_id != anchor.event.operation_id:
            continue
        if authoritative is not None and event.authoritative is not authoritative:
            continue
        if not identity:
            if not _identity_values(event):
                matched.append(item)
            continue
        relation = _identity_relation(identity, event)
        if relation == "matched":
            matched.append(item)
        elif (
            relation == "divergent"
            and _shares_equal_typed_identity(identity, event)
        ):
            conflicting.append(item)
    return matched, conflicting


def _select_receipt(
    indexed: list[_IndexedEvent],
    *,
    anchor: _IndexedEvent,
    contract: _Contract,
    findings: list[Finding],
    seen: set[tuple[str, str | None]],
) -> _Receipt | None:
    operation_id = anchor.event.operation_id
    anchor_identity = _identity_values(anchor.event)

    status_candidates = [
        item
        for item in indexed
        if item.event.stage == Stage.RECEIPT
        and item.event.key in _RECEIPT_STATUS_KEYS
        and _belongs_to_receipt(
            item.event,
            operation_id=operation_id,
            identity=anchor_identity,
        )
    ]
    identity, identity_valid, identity_present = _identity_profile(
        item.event for item in status_candidates or [anchor]
    )
    if not identity_valid:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_EVIDENCE_CONFLICT",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Receipt status evidence for one operation carries conflicting "
                    "typed payment identities."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    *(item.event for item in status_candidates),
                ],
            ),
        )
        return None

    if not identity_present:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_SETTLEMENT_IDENTITY_UNRESOLVED",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                severity="medium",
                explanation=(
                    "The receipt exposes neither authorization_id nor payment_id, "
                    "so it cannot be bound to rail evidence."
                ),
                operation_id=operation_id,
                evidence=[*contract.events, anchor.event],
            ),
        )
        identity = {}

    invalid_status_candidates = [
        item
        for item in status_candidates
        if _canonical_payment_status(item.event.value) is None
    ]
    if invalid_status_candidates:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_STATUS_EVIDENCE_INVALID",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                severity="medium",
                explanation=(
                    "One or more receipt status records for the same typed "
                    "payment identity are not recognized."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    *(item.event for item in invalid_status_candidates),
                ],
            ),
        )
        return None

    canonical_statuses = {
        status
        for item in status_candidates
        if (status := _canonical_payment_status(item.event.value)) is not None
    }
    if len(canonical_statuses) > 1:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_EVIDENCE_CONFLICT",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Receipt status records for the same typed payment identity "
                    "disagree about success versus failure."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    *(item.event for item in status_candidates),
                ],
            ),
        )
        return None

    status_value = _canonical_payment_status(anchor.event.value)
    if status_value is None:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_STATUS_EVIDENCE_INVALID",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                severity="medium",
                explanation=(
                    f"Receipt status {anchor.event.value!r} is not a recognized "
                    "success or failure state."
                ),
                operation_id=operation_id,
                evidence=[*contract.events, anchor.event],
            ),
        )
        return None

    integrity_candidates, integrity_identity_conflicts = (
        _same_receipt_candidates(
            indexed,
            anchor=anchor,
            key="receipt_integrity_status",
            identity=identity,
            authoritative=True,
        )
    )
    integrity_states = {
        status
        for item in integrity_candidates
        if (status := _status(item.event.value)) is not None
    }
    has_verified = bool(integrity_states & _INTEGRITY_VERIFIED)
    has_failed = bool(integrity_states & _INTEGRITY_FAILED)
    integrity: _IndexedEvent | None = None
    integrity_verified = False
    evidence_conflict = False

    if integrity_identity_conflicts:
        evidence_conflict = True
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_EVIDENCE_CONFLICT",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Receipt integrity evidence shares one typed identifier with "
                    "the receipt but conflicts on another."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    anchor.event,
                    *(item.event for item in integrity_identity_conflicts),
                ],
            ),
        )
    elif has_verified and has_failed:
        evidence_conflict = True
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_INTEGRITY_EVIDENCE_CONFLICT",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Authoritative receipt-integrity checks disagree for the same "
                    "typed receipt identity."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    *(item.event for item in integrity_candidates),
                ],
            ),
        )
    elif has_failed:
        integrity = integrity_candidates[-1]
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_INTEGRITY_FAILED",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Independent receipt signature/integrity verification failed."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    *(item.event for item in integrity_candidates),
                ],
            ),
        )
    elif has_verified:
        integrity = integrity_candidates[-1]
        integrity_verified = True
    else:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_INTEGRITY_EVIDENCE_MISSING",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.RECEIPT,
                severity="medium",
                explanation=(
                    "The supplied trace does not contain an authoritative successful "
                    "receipt signature/integrity verification result."
                ),
                operation_id=operation_id,
                evidence=[*contract.events, anchor.event],
            ),
        )

    confirmation_candidates, confirmation_identity_conflicts = (
        _same_receipt_candidates(
            indexed,
            anchor=anchor,
            key="receipt_rail_confirmation",
            identity=identity,
        )
    )
    parsed_confirmations = [
        parsed
        for item in confirmation_candidates
        if (parsed := _confirmation(item)) is not None
    ]
    confirmation: _Confirmation | None = None
    if confirmation_identity_conflicts:
        evidence_conflict = True
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_EVIDENCE_CONFLICT",
                from_stage=Stage.RECEIPT,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Receipt confirmation evidence shares one typed identifier "
                    "with the receipt but conflicts on another."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    anchor.event,
                    *(item.event for item in confirmation_identity_conflicts),
                ],
            ),
        )
    elif parsed_confirmations:
        fingerprints = {
            (
                item.psp_confirmation_id,
                item.network_confirmation_id,
                item.verified_claim,
            )
            for item in parsed_confirmations
        }
        if len(fingerprints) > 1:
            evidence_conflict = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="RECEIPT_EVIDENCE_CONFLICT",
                    from_stage=Stage.RECEIPT,
                    to_stage=Stage.RECEIPT,
                    severity="high",
                    explanation=(
                        "Receipt confirmation records for the same typed payment "
                        "identity disagree."
                    ),
                    operation_id=operation_id,
                    evidence=[
                        *contract.events,
                        *(item.indexed.event for item in parsed_confirmations),
                    ],
                ),
            )
        else:
            confirmation = parsed_confirmations[-1]

    if status_value == "settled" and confirmation is None and not evidence_conflict:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_RAIL_CONFIRMATION_EVIDENCE_MISSING",
                from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                to_stage=Stage.RECEIPT,
                severity="medium",
                explanation=(
                    "The Success receipt lacks one valid PSP/network confirmation "
                    "claim bound to its typed payment identity."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    anchor.event,
                    *(item.event for item in confirmation_candidates),
                ],
            ),
        )

    return _Receipt(
        status=anchor,
        status_value=status_value,
        identity=identity,
        integrity=integrity,
        integrity_verified=integrity_verified,
        confirmation=confirmation,
        evidence_conflict=evidence_conflict,
    )


def _select_rail_evidence(
    indexed: list[_IndexedEvent],
    *,
    receipt: _Receipt,
    contract: _Contract,
    findings: list[Finding],
    seen: set[tuple[str, str | None]],
) -> _RailSelection:
    operation_id = receipt.status.event.operation_id
    matching_statuses: list[_IndexedEvent] = []
    unresolved_statuses: list[_IndexedEvent] = []
    divergent_statuses: list[_IndexedEvent] = []
    matched_identity_wrong_operation: list[_IndexedEvent] = []

    for item in indexed:
        event = item.event
        if (
            event.stage != Stage.ACTUAL_SETTLEMENT_FINALITY
            or event.key != "payment_status"
            or not event.authoritative
        ):
            continue
        relation = _identity_relation(receipt.identity, event)
        if relation == "matched" and event.operation_id == operation_id:
            matching_statuses.append(item)
        elif relation == "matched":
            matched_identity_wrong_operation.append(item)
        elif event.operation_id == operation_id and relation == "unresolved":
            unresolved_statuses.append(item)
        elif (
            event.operation_id == operation_id
            and relation == "divergent"
            and _shares_equal_typed_identity(receipt.identity, event)
        ):
            divergent_statuses.append(item)

    identity_divergent = bool(
        divergent_statuses or matched_identity_wrong_operation
    )
    identity_unresolved = bool(
        unresolved_statuses or not receipt.identity
    ) and not matching_statuses

    if identity_divergent:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_SETTLEMENT_IDENTITY_DIVERGENCE",
                from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Authoritative rail status evidence conflicts with the receipt's "
                    "typed payment identity or operation binding."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    receipt.status.event,
                    *(item.event for item in divergent_statuses),
                    *(item.event for item in matched_identity_wrong_operation),
                ],
            ),
        )
    elif identity_unresolved:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_SETTLEMENT_IDENTITY_UNRESOLVED",
                from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                to_stage=Stage.RECEIPT,
                severity="medium",
                explanation=(
                    "Rail status evidence cannot be tied to the receipt through a "
                    "common typed payment identity and operation."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    receipt.status.event,
                    *(item.event for item in unresolved_statuses),
                ],
            ),
        )

    usable_statuses = [] if identity_divergent else matching_statuses
    status_before = [
        item
        for item in usable_statuses
        if _no_later_than(item, receipt.status)
        and _canonical_payment_status(item.event.value) is not None
    ]
    status_after = [
        item
        for item in usable_statuses
        if not _no_later_than(item, receipt.status)
        and _canonical_payment_status(item.event.value) is not None
    ]
    ordered_before = _chronological(status_before)
    ordered_after = _chronological(status_after)
    status_before_receipt = ordered_before[-1] if ordered_before else None
    status_after_receipt = ordered_after[-1] if ordered_after else None

    matching_confirmations: list[_IndexedEvent] = []
    unresolved_confirmations: list[_IndexedEvent] = []
    divergent_confirmations: list[_IndexedEvent] = []
    matched_confirmation_wrong_operation: list[_IndexedEvent] = []
    for item in indexed:
        event = item.event
        if (
            event.stage != Stage.ACTUAL_SETTLEMENT_FINALITY
            or event.key != "rail_confirmation"
            or not event.authoritative
        ):
            continue
        relation = _identity_relation(receipt.identity, event)
        if relation == "matched" and event.operation_id == operation_id:
            matching_confirmations.append(item)
        elif relation == "matched":
            matched_confirmation_wrong_operation.append(item)
        elif event.operation_id == operation_id and relation == "unresolved":
            unresolved_confirmations.append(item)
        elif (
            event.operation_id == operation_id
            and relation == "divergent"
            and _shares_equal_typed_identity(receipt.identity, event)
        ):
            divergent_confirmations.append(item)

    confirmation_identity_divergent = bool(
        divergent_confirmations or matched_confirmation_wrong_operation
    )
    confirmation_identity_unresolved = bool(
        unresolved_confirmations or not receipt.identity
    ) and not matching_confirmations

    if confirmation_identity_divergent:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_SETTLEMENT_IDENTITY_DIVERGENCE",
                from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    "Authoritative rail-confirmation evidence conflicts with the "
                    "receipt's typed payment identity or operation binding."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    receipt.status.event,
                    *(item.event for item in divergent_confirmations),
                    *(item.event for item in matched_confirmation_wrong_operation),
                ],
            ),
        )
    elif confirmation_identity_unresolved:
        _append_unique(
            findings,
            seen,
            _finding(
                code="RECEIPT_SETTLEMENT_IDENTITY_UNRESOLVED",
                from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                to_stage=Stage.RECEIPT,
                severity="medium",
                explanation=(
                    "Rail-confirmation evidence cannot be tied to the receipt "
                    "through a common typed payment identity and operation."
                ),
                operation_id=operation_id,
                evidence=[
                    *contract.events,
                    receipt.status.event,
                    *(item.event for item in unresolved_confirmations),
                ],
            ),
        )

    usable_confirmation_events = (
        []
        if confirmation_identity_divergent
        else matching_confirmations
    )
    parsed = [
        confirmation
        for item in usable_confirmation_events
        if (confirmation := _confirmation(item)) is not None
    ]
    evidence_conflict = False
    if parsed:
        fingerprints = {
            (
                confirmation.psp_confirmation_id,
                confirmation.network_confirmation_id,
            )
            for confirmation in parsed
        }
        if len(fingerprints) > 1:
            evidence_conflict = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="RECEIPT_RAIL_EVIDENCE_CONFLICT",
                    from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    to_stage=Stage.RECEIPT,
                    severity="high",
                    explanation=(
                        "Authoritative rail-confirmation records for the same typed "
                        "payment identity disagree."
                    ),
                    operation_id=operation_id,
                    evidence=[
                        *contract.events,
                        receipt.status.event,
                        *(confirmation.indexed.event for confirmation in parsed),
                    ],
                ),
            )

    if evidence_conflict:
        confirmation_before_receipt = None
        confirmation_after_receipt = None
    else:
        confirmation_before = [
            item
            for item in parsed
            if _no_later_than(item.indexed, receipt.status)
        ]
        confirmation_after = [
            item
            for item in parsed
            if not _no_later_than(item.indexed, receipt.status)
        ]
        ordered_confirmation_before = _chronological_confirmations(
            confirmation_before
        )
        ordered_confirmation_after = _chronological_confirmations(
            confirmation_after
        )
        confirmation_before_receipt = (
            ordered_confirmation_before[-1]
            if ordered_confirmation_before
            else None
        )
        confirmation_after_receipt = (
            ordered_confirmation_after[0]
            if ordered_confirmation_after
            else None
        )

    return _RailSelection(
        status_before_receipt=status_before_receipt,
        status_after_receipt=status_after_receipt,
        confirmation_before_receipt=confirmation_before_receipt,
        confirmation_after_receipt=confirmation_after_receipt,
        identity_unresolved=identity_unresolved
        or confirmation_identity_unresolved,
        identity_divergent=identity_divergent
        or confirmation_identity_divergent,
        evidence_conflict=evidence_conflict,
    )


def verify_independent_receipt_finality_binding(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify signed receipt success against independent payment-rail truth.

    Receipt integrity proves authenticity of a claim. Confirmation IDs and an
    issuer-provided ``rail_confirmation_verified`` flag remain receipt claims.
    A Success receipt becomes independently verified only when authoritative
    finality and confirmation evidence for the same typed payment identity and
    operation existed no later than receipt issuance.
    """

    indexed = [
        _IndexedEvent(index, event)
        for index, event in enumerate(events)
    ]
    findings: list[Finding] = []
    seen: set[tuple[str, str | None]] = set()

    contract_groups: dict[str | None, list[StateEvent]] = {}
    invalid_contracts: set[str | None] = set()
    for item in indexed:
        event = item.event
        if event.stage not in {
            Stage.QUOTE_CHALLENGE,
            Stage.MANDATE_AUTHORIZATION,
            Stage.POLICY_DECISION,
        }:
            continue
        if event.key != "requires_independent_receipt_finality_binding":
            continue
        required, valid = _contract(event.value)
        if not required:
            continue
        if not valid:
            invalid_contracts.add(event.operation_id)
            _append_unique(
                findings,
                seen,
                _finding(
                    code="RECEIPT_FINALITY_BINDING_CONTRACT_INVALID",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RECEIPT,
                    severity="medium",
                    explanation=(
                        "The receipt contract must require settled finality for "
                        "Success and classify issuer verification flags as claims."
                    ),
                    operation_id=event.operation_id,
                    evidence=[event],
                ),
            )
            continue
        contract_groups.setdefault(event.operation_id, []).append(event)

    if None in invalid_contracts:
        return findings

    contracts = {
        operation_id: _Contract(events=tuple(group))
        for operation_id, group in contract_groups.items()
        if operation_id not in invalid_contracts
    }
    if not contracts:
        return findings

    receipt_statuses = [
        item
        for item in indexed
        if item.event.stage == Stage.RECEIPT
        and item.event.key in _RECEIPT_STATUS_KEYS
    ]
    operations = set(contracts)
    operations.update(
        item.event.operation_id
        for item in receipt_statuses
        if _applicable_contract(contracts, item.event.operation_id) is not None
    )

    processed_identities: set[
        tuple[
            str | None,
            tuple[tuple[str, str], ...] | tuple[str, int],
        ]
    ] = set()

    for operation_id in sorted(
        operations,
        key=lambda value: "" if value is None else value,
    ):
        if operation_id in invalid_contracts:
            continue
        contract = _applicable_contract(contracts, operation_id)
        if contract is None:
            continue
        anchors = [
            item
            for item in receipt_statuses
            if item.event.operation_id == operation_id
        ]
        if not anchors:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="RECEIPT_STATUS_EVIDENCE_MISSING",
                    from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    to_stage=Stage.RECEIPT,
                    severity="medium",
                    explanation=(
                        "Independent receipt-finality verification is required, "
                        "but no receipt status claim appears in the trace."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events],
                ),
            )
            continue

        for anchor in anchors:
            anchor_identity = _identity_values(anchor.event)
            identity_key: (
                tuple[tuple[str, str], ...]
                | tuple[str, int]
            )
            if anchor_identity:
                identity_key = tuple(sorted(anchor_identity.items()))
            else:
                identity_key = ("event_index", anchor.index)
            process_key = (operation_id, identity_key)
            if process_key in processed_identities:
                continue
            processed_identities.add(process_key)

            receipt = _select_receipt(
                indexed,
                anchor=anchor,
                contract=contract,
                findings=findings,
                seen=seen,
            )
            if receipt is None or receipt.status_value != "settled":
                continue

            rail = _select_rail_evidence(
                indexed,
                receipt=receipt,
                contract=contract,
                findings=findings,
                seen=seen,
            )
            finality_status = (
                _canonical_payment_status(
                    rail.status_before_receipt.event.value
                )
                if rail.status_before_receipt is not None
                else None
            )
            later_finality_status = (
                _canonical_payment_status(
                    rail.status_after_receipt.event.value
                )
                if rail.status_after_receipt is not None
                else None
            )
            finality_conflict = bool(
                finality_status == "failed"
                or (
                    finality_status is None
                    and later_finality_status == "failed"
                )
            )
            if finality_conflict:
                conflict_event = (
                    rail.status_before_receipt
                    if finality_status == "failed"
                    else rail.status_after_receipt
                )
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="RECEIPT_SUCCESS_FINALITY_CONFLICT",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECEIPT,
                        severity="critical",
                        explanation=(
                            "The signed receipt claims Success while authoritative "
                            "rail finality for the same payment is non-settled."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            receipt.status.event,
                            conflict_event.event if conflict_event else None,
                        ],
                    ),
                )
            elif finality_status != "settled":
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECEIPT,
                        severity="medium",
                        explanation=(
                            "The receipt claims Success without matching authoritative "
                            "settled finality that existed no later than issuance."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            receipt.status.event,
                            rail.status_after_receipt.event
                            if rail.status_after_receipt
                            else None,
                        ],
                    ),
                )

            receipt_confirmation = receipt.confirmation
            rail_confirmation = rail.confirmation_before_receipt
            if (
                rail_confirmation is None
                and not rail.evidence_conflict
                and not receipt.evidence_conflict
            ):
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="RECEIPT_RAIL_CONFIRMATION_EVIDENCE_MISSING",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECEIPT,
                        severity="medium",
                        explanation=(
                            "No authoritative PSP/network confirmation record for "
                            "the same payment existed no later than receipt issuance."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            receipt.status.event,
                            receipt_confirmation.indexed.event
                            if receipt_confirmation
                            else None,
                            rail.confirmation_after_receipt.indexed.event
                            if rail.confirmation_after_receipt
                            else None,
                        ],
                    ),
                )

            comparison_confirmation = (
                rail_confirmation or rail.confirmation_after_receipt
            )
            ids_match = bool(
                receipt_confirmation
                and rail_confirmation
                and receipt_confirmation.psp_confirmation_id
                == rail_confirmation.psp_confirmation_id
                and receipt_confirmation.network_confirmation_id
                == rail_confirmation.network_confirmation_id
            )
            ids_conflict = bool(
                receipt_confirmation
                and comparison_confirmation
                and (
                    receipt_confirmation.psp_confirmation_id
                    != comparison_confirmation.psp_confirmation_id
                    or receipt_confirmation.network_confirmation_id
                    != comparison_confirmation.network_confirmation_id
                )
            )
            if (
                ids_conflict
                and not rail.evidence_conflict
            ):
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="RECEIPT_CONFIRMATION_ID_MISMATCH",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECEIPT,
                        severity="high",
                        explanation=(
                            "Receipt PSP/network confirmation IDs do not match "
                            "the authoritative rail confirmation record."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            receipt_confirmation.indexed.event,
                            comparison_confirmation.indexed.event,
                        ],
                    ),
                )

            verified_claim = bool(
                receipt_confirmation
                and receipt_confirmation.verified_claim is True
            )
            independent_support = bool(
                finality_status == "settled"
                and ids_match
                and receipt.integrity_verified
                and not receipt.evidence_conflict
                and not rail.evidence_conflict
                and not rail.identity_unresolved
                and not rail.identity_divergent
            )

            if verified_claim and not independent_support:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="RECEIPT_RAIL_VERIFICATION_CLAIM_UNSUPPORTED",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECEIPT,
                        severity="medium",
                        explanation=(
                            "The issuer claims rail_confirmation_verified=true, but "
                            "the trace lacks complete matching independent evidence "
                            "that existed at receipt issuance."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            receipt.status.event,
                            receipt_confirmation.indexed.event
                            if receipt_confirmation
                            else None,
                            rail.status_before_receipt.event
                            if rail.status_before_receipt
                            else None,
                            rail_confirmation.indexed.event
                            if rail_confirmation
                            else None,
                        ],
                    ),
                )

            late_confirmation_match = bool(
                receipt_confirmation
                and rail.confirmation_after_receipt
                and receipt_confirmation.psp_confirmation_id
                == rail.confirmation_after_receipt.psp_confirmation_id
                and receipt_confirmation.network_confirmation_id
                == rail.confirmation_after_receipt.network_confirmation_id
            )
            late_support = bool(
                verified_claim
                and (
                    later_finality_status == "settled"
                    or late_confirmation_match
                )
            )
            if late_support:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="RECEIPT_VERIFICATION_TIMING_UNPROVEN",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECEIPT,
                        severity="medium",
                        explanation=(
                            "Matching rail evidence appears only after receipt "
                            "issuance, so the receipt's claim of prior independent "
                            "verification is not established."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            receipt.status.event,
                            rail.status_after_receipt.event
                            if rail.status_after_receipt
                            else None,
                            rail.confirmation_after_receipt.indexed.event
                            if rail.confirmation_after_receipt
                            else None,
                        ],
                    ),
                )

    return findings


__all__ = ["verify_independent_receipt_finality_binding"]
