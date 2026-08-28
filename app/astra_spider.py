from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    observed_at: int | float | str | None = None


@dataclass(frozen=True)
class Finding:
    code: str
    from_stage: Stage
    to_stage: Stage
    severity: str
    explanation: str
    evidence_sources: tuple[str, ...] = ()


def _matching(
    events: list[StateEvent],
    stage: Stage,
    keys: set[str],
    authoritative: bool | None = None,
) -> list[StateEvent]:
    matched = [e for e in events if e.stage == stage and e.key in keys]
    return (
        matched
        if authoritative is None
        else [e for e in matched if e.authoritative is authoritative]
    )


def _latest(
    events: list[StateEvent],
    stages: set[Stage],
    keys: set[str],
) -> StateEvent | None:
    matched = [e for e in events if e.stage in stages and e.key in keys]
    return matched[-1] if matched else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is not None:
        return numeric
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _add(
    findings: list[Finding],
    code: str,
    edge: tuple[Stage, Stage],
    severity: str,
    explanation: str,
    *evidence: StateEvent | None,
) -> None:
    findings.append(
        Finding(
            code=code,
            from_stage=edge[0],
            to_stage=edge[1],
            severity=severity,
            explanation=explanation,
            evidence_sources=tuple(
                dict.fromkeys(e.source for e in evidence if e is not None)
            ),
        )
    )


def _challenge_expiry(
    events: list[StateEvent],
) -> tuple[float | None, tuple[StateEvent | None, ...]]:
    explicit = _latest(events, {Stage.QUOTE_CHALLENGE}, {"challenge_expires_at"})
    if explicit:
        return _epoch(explicit.value), (explicit,)
    issued = _latest(events, {Stage.QUOTE_CHALLENGE}, {"challenge_issued_at"})
    timeout = _latest(events, {Stage.QUOTE_CHALLENGE}, {"max_timeout_seconds"})
    issued_at = _epoch(issued.value) if issued else None
    seconds = _number(timeout.value) if timeout else None
    return (
        (issued_at + seconds, (issued, timeout))
        if issued_at is not None and seconds is not None
        else (None, (issued, timeout))
    )


def verify_causal_economic_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Check protocol-neutral causal/economic invariants.

    ``authoritative=True`` is integration-supplied. Findings describe only the
    supplied trace; missing evidence is not proof of global event absence.
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
        stale = next(
            (
                e
                for e in _matching(
                    events,
                    Stage.ACTUAL_SETTLEMENT_FINALITY,
                    {"payment_status"},
                    authoritative=True,
                )
                if e.value == "settled"
                and _epoch(e.observed_at) is not None
                and challenge_expiry < _epoch(e.observed_at) <= authorization_expiry
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

    # Retry identity: reusing an idempotency key is safe when its context is stable.
    attempts = _matching(events, Stage.PAYMENT_ATTEMPT, {"attempt"})
    by_attempt: dict[str, list[StateEvent]] = {}
    for event in attempts:
        if event.attempt_id:
            by_attempt.setdefault(event.attempt_id, []).append(event)
    for attempt_id, group in by_attempt.items():
        dimensions = (
            {e.payload_hash for e in group if e.payload_hash},
            {e.session_id for e in group if e.session_id},
            {e.operation_id for e in group if e.operation_id},
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
    if claimed and not finality:
        code, severity = (
            ("CLAIMED_SETTLED_WITHOUT_FINALITY", "critical")
            if claimed.value == "settled"
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
    elif claimed and finality and claimed.value != finality.value:
        if claimed.value in {"failed", "rejected", "not_settled"} and finality.value == "settled":
            code, severity = "CLAIMED_FAILED_BUT_SETTLED", "critical"
        elif claimed.value == "settled":
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

    # PAYMENT ATTEMPT -> SETTLEMENT: duplicate spend and over-capture.
    settled_ids = {
        e.payment_id for e in finalities if e.value == "settled" and e.payment_id
    }
    mode_event = _latest(
        events,
        {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION},
        {"settlement_mode", "payment_flow"},
    )
    multi_modes = {
        "batch",
        "partial",
        "installment",
        "multi_capture",
        "auth-capture",
        "auth_capture",
        "escrow",
    }
    mode = str(mode_event.value).lower() if mode_event else None
    if len(settled_ids) > 1 and mode not in multi_modes:
        _add(
            findings,
            "RETRY_DUPLICATE_PAYMENT",
            (Stage.PAYMENT_ATTEMPT, Stage.ACTUAL_SETTLEMENT_FINALITY),
            "critical",
            "Multiple payments settled without an explicit multi-settlement mode.",
            *finalities,
        )

    authorized = _latest(
        events,
        {Stage.MANDATE_AUTHORIZATION},
        {"authorized_amount_minor", "max_amount_minor", "mandate_amount_minor"},
    )
    amount_events = [
        e
        for e in events
        if e.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and e.authoritative
        and e.key in {
            "settled_amount_minor",
            "captured_amount_minor",
            "charged_amount_minor",
        }
        and _number(e.value) is not None
    ]
    authorized_amount = _number(authorized.value) if authorized else None
    settled_total = sum(_number(e.value) or 0 for e in amount_events)
    if authorized_amount is not None and amount_events and settled_total > authorized_amount:
        _add(
            findings,
            "OVER_CAPTURE",
            (Stage.MANDATE_AUTHORIZATION, Stage.ACTUAL_SETTLEMENT_FINALITY),
            "critical",
            f"Captured {settled_total:g}; authorization permits {authorized_amount:g}.",
            authorized,
            *amount_events,
        )

    # PAYMENT-RESPONSE -> client ledger (x402 batch-settlement class).
    before = _latest(
        events,
        {Stage.PAYMENT_ATTEMPT},
        {"client_ledger_before_minor"},
    )
    charge = _latest(events, {Stage.CLAIMED_RESULT}, {"charged_amount_minor"})
    cumulative = _latest(
        events,
        {Stage.CLAIMED_RESULT},
        {"channel_state_cumulative_minor"},
    )
    after = _latest(
        events,
        {Stage.RECONCILIATION},
        {"client_ledger_after_minor"},
    )
    before_value = _number(before.value) if before else None
    charge_value = _number(charge.value) if charge else None
    cumulative_value = _number(cumulative.value) if cumulative else None
    after_value = _number(after.value) if after else None
    if None not in {before_value, charge_value, cumulative_value}:
        expected = before_value + charge_value
        if cumulative_value != expected:
            _add(
                findings,
                "UNTRUSTED_CLAIMED_LEDGER_STATE",
                (Stage.CLAIMED_RESULT, Stage.RECONCILIATION),
                "high",
                f"Claimed cumulative {cumulative_value:g}; local derivation is {expected:g}.",
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

    # SETTLEMENT/FINALITY -> RECEIPT -> DELIVERY -> RECONCILIATION
    delivered = _latest(
        events,
        {Stage.RESOURCE_OUTCOME_DELIVERY},
        {"delivery_status"},
    )
    is_delivered = bool(delivered and delivered.value in {"delivered", "complete"})
    is_settled = bool(finality and finality.value == "settled")

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
        and capture.value in {"captured", "settled", "complete"}
    )
    deferred_gap = bool(
        is_delivered
        and capture_mode
        and str(capture_mode.value).lower() == "deferred"
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
    if receipt and finality and receipt.value != finality.value:
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
    elif (
        is_settled
        and is_delivered
        and str(reconciliation.value).lower() not in terminal
    ):
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
