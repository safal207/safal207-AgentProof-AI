from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable


class Stage(str, Enum):
    REQUEST = "REQUEST"
    QUOTE_CHALLENGE = "QUOTE/CHALLENGE"
    MANDATE_AUTHORIZATION = "MANDATE/AUTHORIZATION"
    POLICY_DECISION = "POLICY DECISION"
    PAYMENT_ATTEMPT = "PAYMENT ATTEMPT"
    CLAIMED_RESULT = "CLAIMED RESULT"
    ACTUAL_SETTLEMENT_FINALITY = "ACTUAL SETTLEMENT/FINALITY"
    RECEIPT = "RECEIPT"
    RESOURCE_OUTCOME_DELIVERY = "RESOURCE/OUTCOME DELIVERY"
    RECONCILIATION = "RECONCILIATION"


STATE_GRAPH: tuple[Stage, ...] = tuple(Stage)


@dataclass(frozen=True)
class StateEvent:
    stage: Stage
    key: str
    value: Any
    source: str
    authoritative: bool = False
    attempt_id: str | None = None
    payment_id: str | None = None
    operation_id: str | None = None
    session_id: str | None = None
    authorization_id: str | None = None
    payload_hash: str | None = None
    observed_at: int | float | Decimal | str | datetime | None = None


@dataclass(frozen=True)
class Finding:
    code: str
    from_stage: Stage
    to_stage: Stage
    severity: str
    explanation: str
    evidence_sources: tuple[str, ...] = ()
    operation_id: str | None = None


def _matching(
    events: list[StateEvent],
    stage: Stage,
    keys: set[str],
    authoritative: bool | None = None,
) -> list[StateEvent]:
    matched = [event for event in events if event.stage == stage and event.key in keys]
    if authoritative is None:
        return matched
    return [event for event in matched if event.authoritative is authoritative]


def _latest(
    events: list[StateEvent],
    stages: set[Stage],
    keys: set[str],
) -> StateEvent | None:
    """Return the last matching event in supplied trace order."""

    matched = [event for event in events if event.stage in stages and event.key in keys]
    return matched[-1] if matched else None


def _latest_for_operation(
    events: list[StateEvent],
    operation_id: str,
    stages: set[Stage],
    keys: set[str],
) -> StateEvent | None:
    specific = [
        event
        for event in events
        if event.stage in stages
        and event.key in keys
        and event.operation_id == operation_id
    ]
    if specific:
        return specific[-1]
    global_events = [
        event
        for event in events
        if event.stage in stages
        and event.key in keys
        and event.operation_id is None
    ]
    return global_events[-1] if global_events else None


def _decimal(value: Any) -> Decimal | None:
    """Parse an exact finite decimal without binary-float arithmetic."""

    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            parsed = value
        elif isinstance(value, int):
            parsed = Decimal(value)
        elif isinstance(value, float):
            parsed = Decimal(str(value))
        elif isinstance(value, str):
            parsed = Decimal(value.strip())
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _epoch(value: Any) -> Decimal | None:
    numeric = _decimal(value)
    if numeric is not None:
        return numeric

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return None
    return Decimal(str(parsed.timestamp()))


def _status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _operation_from(evidence: tuple[StateEvent | None, ...]) -> str | None:
    operation_ids = {
        event.operation_id
        for event in evidence
        if event is not None and event.operation_id is not None
    }
    return next(iter(operation_ids)) if len(operation_ids) == 1 else None


def _add(
    findings: list[Finding],
    code: str,
    edge: tuple[Stage, Stage],
    severity: str,
    explanation: str,
    *evidence: StateEvent | None,
    operation_id: str | None = None,
) -> None:
    findings.append(
        Finding(
            code=code,
            from_stage=edge[0],
            to_stage=edge[1],
            severity=severity,
            explanation=explanation,
            evidence_sources=tuple(
                dict.fromkeys(
                    event.source
                    for event in evidence
                    if event is not None and event.source
                )
            ),
            operation_id=operation_id or _operation_from(evidence),
        )
    )


def _challenge_expiry(
    events: list[StateEvent],
) -> tuple[Decimal | None, tuple[StateEvent | None, ...]]:
    explicit = _latest(events, {Stage.QUOTE_CHALLENGE}, {"challenge_expires_at"})
    if explicit:
        return _epoch(explicit.value), (explicit,)

    issued = _latest(events, {Stage.QUOTE_CHALLENGE}, {"challenge_issued_at"})
    timeout = _latest(events, {Stage.QUOTE_CHALLENGE}, {"max_timeout_seconds"})
    issued_at = _epoch(issued.value) if issued else None
    seconds = _decimal(timeout.value) if timeout else None
    if issued_at is None or seconds is None:
        return None, (issued, timeout)
    return issued_at + seconds, (issued, timeout)


def _expected_settlement_count(event: StateEvent | None) -> int | None:
    if event is None:
        return None
    value = _decimal(event.value)
    if value is None or value <= 0 or value != value.to_integral_value():
        return None
    return int(value)


def _preferred_amount_events(
    events: list[StateEvent],
    operation_id: str,
) -> list[StateEvent]:
    operation_events = [
        event
        for event in events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.authoritative
        and (event.operation_id or "__trace__") == operation_id
    ]
    for key in (
        "captured_amount_minor",
        "charged_amount_minor",
        "settled_amount_minor",
    ):
        selected = [
            event
            for event in operation_events
            if event.key == key and _decimal(event.value) is not None
        ]
        if selected:
            return selected
    return []


def verify_causal_economic_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Check protocol-neutral causal and economic invariants.

    Event order is trace order. ``authoritative=True`` is supplied by the
    integration, not inferred by the core. Findings describe only the supplied
    evidence and never prove global event absence.
    """

    events = list(events)
    findings: list[Finding] = []

    # QUOTE/CHALLENGE -> MANDATE/AUTHORIZATION
    challenge_expiry, challenge_evidence = _challenge_expiry(events)
    authorization = _latest(
        events,
        {Stage.MANDATE_AUTHORIZATION},
        {"authorization_valid_before"},
    )
    authorization_expiry = _epoch(authorization.value) if authorization else None
    if challenge_expiry is not None and authorization_expiry is not None:
        authorization_operation = authorization.operation_id if authorization else None
        stale = next(
            (
                event
                for event in _matching(
                    events,
                    Stage.ACTUAL_SETTLEMENT_FINALITY,
                    {"payment_status"},
                    authoritative=True,
                )
                if _status(event.value) == "settled"
                and (
                    authorization_operation is None
                    or event.operation_id in {None, authorization_operation}
                )
                and (observed_at := _epoch(event.observed_at)) is not None
                and challenge_expiry < observed_at <= authorization_expiry
            ),
            None,
        )
        if stale:
            _add(
                findings,
                "STALE_AUTHORIZATION_SETTLED",
                (Stage.QUOTE_CHALLENGE, Stage.ACTUAL_SETTLEMENT_FINALITY),
                "critical",
                "Settlement occurred after the advertised challenge window expired.",
                *challenge_evidence,
                authorization,
                stale,
            )
        elif authorization_expiry > challenge_expiry:
            _add(
                findings,
                "AUTHORIZATION_OUTLIVES_CHALLENGE",
                (Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION),
                "high",
                "The signed authorization outlives the advertised challenge window.",
                *challenge_evidence,
                authorization,
            )

    # PAYMENT ATTEMPT -> PAYMENT ATTEMPT
    attempts = _matching(events, Stage.PAYMENT_ATTEMPT, {"attempt"})
    by_attempt: dict[str, list[StateEvent]] = {}
    for event in attempts:
        if event.attempt_id:
            by_attempt.setdefault(event.attempt_id, []).append(event)
    for attempt_id, group in by_attempt.items():
        dimensions = (
            {event.payload_hash for event in group if event.payload_hash},
            {event.session_id for event in group if event.session_id},
            {event.operation_id for event in group if event.operation_id},
        )
        if any(len(values) > 1 for values in dimensions):
            _add(
                findings,
                "ATTEMPT_ID_COLLISION",
                (Stage.PAYMENT_ATTEMPT, Stage.PAYMENT_ATTEMPT),
                "high",
                f"Attempt {attempt_id!r} is reused across divergent context.",
                *group,
            )

    # CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY
    claimed = _latest(events, {Stage.CLAIMED_RESULT}, {"payment_status"})
    finalities = _matching(
        events,
        Stage.ACTUAL_SETTLEMENT_FINALITY,
        {"payment_status"},
        authoritative=True,
    )
    finality = finalities[-1] if finalities else None
    claimed_status = _status(claimed.value) if claimed else None
    finality_status = _status(finality.value) if finality else None
    if claimed and not finality:
        code, severity = (
            ("CLAIMED_SETTLED_WITHOUT_FINALITY", "critical")
            if claimed_status == "settled"
            else ("FINALITY_EVIDENCE_MISSING", "medium")
        )
        _add(
            findings,
            code,
            (Stage.CLAIMED_RESULT, Stage.ACTUAL_SETTLEMENT_FINALITY),
            severity,
            "A terminal result is claimed without independent finality evidence.",
            claimed,
        )
    elif claimed and finality and claimed_status != finality_status:
        if claimed_status in {"failed", "rejected", "not_settled"} and finality_status == "settled":
            code, severity = "CLAIMED_FAILED_BUT_SETTLED", "critical"
        elif claimed_status == "settled":
            code, severity = "CLAIMED_SETTLED_WITHOUT_FINALITY", "critical"
        else:
            code, severity = "CLAIM_FINALITY_DIVERGENCE", "high"
        _add(
            findings,
            code,
            (Stage.CLAIMED_RESULT, Stage.ACTUAL_SETTLEMENT_FINALITY),
            severity,
            f"Claimed state {claimed.value!r} conflicts with {finality.value!r}.",
            claimed,
            finality,
        )

    # PAYMENT ATTEMPT -> ACTUAL SETTLEMENT/FINALITY
    settled_events = [event for event in finalities if _status(event.value) == "settled"]
    settled_by_operation: dict[str, list[StateEvent]] = {}
    for event in settled_events:
        settled_by_operation.setdefault(event.operation_id or "__trace__", []).append(event)

    multi_modes = {
        "batch",
        "partial",
        "installment",
        "multi_capture",
        "auth-capture",
        "auth_capture",
        "escrow",
    }
    for operation_id, group in settled_by_operation.items():
        payment_ids = {event.payment_id for event in group if event.payment_id}
        if len(payment_ids) <= 1:
            continue
        expected_event = _latest_for_operation(
            events,
            operation_id,
            {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION},
            {"expected_settlement_count"},
        )
        expected_count = _expected_settlement_count(expected_event)
        mode_event = _latest_for_operation(
            events,
            operation_id,
            {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION},
            {"settlement_mode", "payment_flow"},
        )
        mode = _status(mode_event.value) if mode_event else None
        if expected_count is not None and len(payment_ids) > expected_count:
            _add(
                findings,
                "RETRY_DUPLICATE_PAYMENT",
                (Stage.PAYMENT_ATTEMPT, Stage.ACTUAL_SETTLEMENT_FINALITY),
                "critical",
                f"{len(payment_ids)} payments settled; at most {expected_count} are expected.",
                expected_event,
                *group,
                operation_id=None if operation_id == "__trace__" else operation_id,
            )
        elif expected_count is None and mode in multi_modes:
            _add(
                findings,
                "MULTI_SETTLEMENT_UNRESOLVED",
                (Stage.PAYMENT_ATTEMPT, Stage.ACTUAL_SETTLEMENT_FINALITY),
                "medium",
                "Multiple settlements are present in a multi-settlement mode, but the expected lifecycle count is absent.",
                mode_event,
                *group,
                operation_id=None if operation_id == "__trace__" else operation_id,
            )
        elif expected_count is None:
            _add(
                findings,
                "RETRY_DUPLICATE_PAYMENT",
                (Stage.PAYMENT_ATTEMPT, Stage.ACTUAL_SETTLEMENT_FINALITY),
                "critical",
                "Multiple distinct payments settled for one ordinary logical operation.",
                *group,
                operation_id=None if operation_id == "__trace__" else operation_id,
            )

    # MANDATE/AUTHORIZATION -> ACTUAL SETTLEMENT/FINALITY
    amount_operations = {
        event.operation_id or "__trace__"
        for event in events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.authoritative
        and event.key
        in {
            "settled_amount_minor",
            "captured_amount_minor",
            "charged_amount_minor",
        }
    }
    for operation_id in amount_operations:
        amount_events = _preferred_amount_events(events, operation_id)
        if not amount_events:
            continue
        authorized = _latest_for_operation(
            events,
            operation_id,
            {Stage.MANDATE_AUTHORIZATION},
            {"authorized_amount_minor", "max_amount_minor", "mandate_amount_minor"},
        )
        authorized_amount = _decimal(authorized.value) if authorized else None
        if authorized_amount is None:
            continue
        settled_total = sum(
            (_decimal(event.value) for event in amount_events),
            start=Decimal(0),
        )
        if settled_total > authorized_amount:
            _add(
                findings,
                "OVER_CAPTURE",
                (Stage.MANDATE_AUTHORIZATION, Stage.ACTUAL_SETTLEMENT_FINALITY),
                "critical",
                f"Captured {settled_total}; authorization permits {authorized_amount}.",
                authorized,
                *amount_events,
                operation_id=None if operation_id == "__trace__" else operation_id,
            )

    # CLAIMED RESULT -> CLIENT LEDGER / RECONCILIATION
    before = _latest(events, {Stage.PAYMENT_ATTEMPT}, {"client_ledger_before_minor"})
    charge = _latest(events, {Stage.CLAIMED_RESULT}, {"charged_amount_minor"})
    cumulative = _latest(
        events,
        {Stage.CLAIMED_RESULT},
        {"channel_state_cumulative_minor"},
    )
    after = _latest(events, {Stage.RECONCILIATION}, {"client_ledger_after_minor"})
    before_value = _decimal(before.value) if before else None
    charge_value = _decimal(charge.value) if charge else None
    cumulative_value = _decimal(cumulative.value) if cumulative else None
    after_value = _decimal(after.value) if after else None
    if before_value is not None and charge_value is not None and cumulative_value is not None:
        expected = before_value + charge_value
        if cumulative_value != expected:
            _add(
                findings,
                "UNTRUSTED_CLAIMED_LEDGER_STATE",
                (Stage.CLAIMED_RESULT, Stage.RECONCILIATION),
                "high",
                f"Claimed cumulative {cumulative_value}; local derivation is {expected}.",
                before,
                charge,
                cumulative,
            )
        if after_value == cumulative_value and after_value != expected:
            _add(
                findings,
                "LEDGER_STATE_DIVERGENCE",
                (Stage.CLAIMED_RESULT, Stage.RECONCILIATION),
                "critical",
                "The client persisted conflicting upstream state as ledger truth.",
                before,
                charge,
                cumulative,
                after,
            )

    # ACTUAL SETTLEMENT/FINALITY -> RECEIPT -> DELIVERY -> RECONCILIATION
    delivered = _latest(
        events,
        {Stage.RESOURCE_OUTCOME_DELIVERY},
        {"delivery_status"},
    )
    is_delivered = _status(delivered.value) in {"delivered", "complete"} if delivered else False
    is_settled = finality_status == "settled"

    capture_mode = _latest(
        events,
        {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION},
        {"capture_mode", "finalize_mode"},
    )
    capture = _latest(
        events,
        {Stage.ACTUAL_SETTLEMENT_FINALITY},
        {"capture_status"},
    )
    capture_complete = bool(
        capture
        and capture.authoritative
        and _status(capture.value) in {"captured", "settled", "complete"}
    )
    deferred_gap = bool(
        is_delivered
        and capture_mode
        and _status(capture_mode.value) == "deferred"
        and not capture_complete
    )
    if deferred_gap:
        _add(
            findings,
            "DELIVERED_WITHOUT_CAPTURE",
            (Stage.RESOURCE_OUTCOME_DELIVERY, Stage.ACTUAL_SETTLEMENT_FINALITY),
            "critical",
            "Delivery is confirmed but deferred capture completion is absent.",
            capture_mode,
            delivered,
            capture,
        )
    if is_settled and not is_delivered:
        _add(
            findings,
            "SETTLED_BUT_NOT_DELIVERED",
            (Stage.ACTUAL_SETTLEMENT_FINALITY, Stage.RESOURCE_OUTCOME_DELIVERY),
            "critical",
            "Authoritative settlement exists without confirmed delivery.",
            finality,
        )
    if is_delivered and not is_settled and not deferred_gap:
        _add(
            findings,
            "DELIVERED_BUT_NOT_SETTLED",
            (Stage.RESOURCE_OUTCOME_DELIVERY, Stage.ACTUAL_SETTLEMENT_FINALITY),
            "high",
            "Delivery is confirmed without authoritative settlement finality.",
            delivered,
        )

    receipt = _latest(events, {Stage.RECEIPT}, {"payment_status"})
    if receipt and finality and _status(receipt.value) != finality_status:
        _add(
            findings,
            "RECEIPT_FINALITY_MISMATCH",
            (Stage.ACTUAL_SETTLEMENT_FINALITY, Stage.RECEIPT),
            "high",
            f"Receipt says {receipt.value!r}; finality says {finality.value!r}.",
            finality,
            receipt,
        )

    reconciliation = _latest(events, {Stage.RECONCILIATION}, {"status"})
    terminal = {
        "complete",
        "completed",
        "reconciled",
        "refunded",
        "reclaimed",
        "voided",
        "cancelled",
        "canceled",
        "closed",
        "terminal",
        "final",
    }
    if is_settled and is_delivered and not reconciliation:
        _add(
            findings,
            "RECONCILIATION_GAP",
            (Stage.RESOURCE_OUTCOME_DELIVERY, Stage.RECONCILIATION),
            "medium",
            "Settlement and delivery exist without reconciliation evidence.",
            finality,
            delivered,
        )
    elif is_settled and is_delivered and _status(reconciliation.value) not in terminal:
        _add(
            findings,
            "RECONCILIATION_NOT_TERMINAL",
            (Stage.RESOURCE_OUTCOME_DELIVERY, Stage.RECONCILIATION),
            "medium",
            f"Reconciliation remains {reconciliation.value!r}.",
            finality,
            delivered,
            reconciliation,
        )

    return findings
