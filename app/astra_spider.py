from __future__ import annotations

from dataclasses import dataclass
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


STATE_GRAPH: tuple[Stage, ...] = (
    Stage.REQUEST,
    Stage.QUOTE_CHALLENGE,
    Stage.MANDATE_AUTHORIZATION,
    Stage.POLICY_DECISION,
    Stage.PAYMENT_ATTEMPT,
    Stage.CLAIMED_RESULT,
    Stage.ACTUAL_SETTLEMENT_FINALITY,
    Stage.RECEIPT,
    Stage.RESOURCE_OUTCOME_DELIVERY,
    Stage.RECONCILIATION,
)


@dataclass(frozen=True)
class StateEvent:
    stage: Stage
    key: str
    value: Any
    source: str
    authoritative: bool = False
    attempt_id: str | None = None
    payment_id: str | None = None


@dataclass(frozen=True)
class Finding:
    code: str
    from_stage: Stage
    to_stage: Stage
    severity: str
    explanation: str


def _events_for(events: Iterable[StateEvent], stage: Stage, key: str) -> list[StateEvent]:
    return [event for event in events if event.stage == stage and event.key == key]


def _latest(events: Iterable[StateEvent], stage: Stage, key: str) -> StateEvent | None:
    matches = _events_for(events, stage, key)
    return matches[-1] if matches else None


def verify_causal_economic_outcome(events: Iterable[StateEvent]) -> list[Finding]:
    """Verify adjacent economic-state relationships without trusting upstream claims.

    The verifier is intentionally protocol-neutral. It treats PSP/chain/finality evidence
    as authoritative only when the caller marks that evidence authoritative, so a header,
    callback, receipt, or facilitator response cannot silently become ledger truth.
    """
    events = list(events)
    findings: list[Finding] = []

    claimed = _latest(events, Stage.CLAIMED_RESULT, "payment_status")
    settled_candidates = [
        event
        for event in _events_for(events, Stage.ACTUAL_SETTLEMENT_FINALITY, "payment_status")
        if event.authoritative
    ]
    settled = settled_candidates[-1] if settled_candidates else None

    if claimed and settled and claimed.value != settled.value:
        if claimed.value in {"failed", "rejected", "not_settled"} and settled.value == "settled":
            code = "CLAIMED_FAILED_BUT_SETTLED"
            severity = "critical"
        elif claimed.value == "settled" and settled.value != "settled":
            code = "CLAIMED_SETTLED_WITHOUT_FINALITY"
            severity = "critical"
        else:
            code = "CLAIM_FINALITY_DIVERGENCE"
            severity = "high"
        findings.append(
            Finding(
                code=code,
                from_stage=Stage.CLAIMED_RESULT,
                to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                severity=severity,
                explanation=(
                    f"Claimed payment state {claimed.value!r} from {claimed.source} conflicts "
                    f"with authoritative state {settled.value!r} from {settled.source}."
                ),
            )
        )

    attempts = [event for event in events if event.stage == Stage.PAYMENT_ATTEMPT and event.key == "attempt"]
    settled_payment_ids = {
        event.payment_id
        for event in events
        if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
        and event.key == "payment_status"
        and event.value == "settled"
        and event.authoritative
        and event.payment_id
    }
    attempt_ids = [event.attempt_id for event in attempts if event.attempt_id]
    if len(attempt_ids) != len(set(attempt_ids)):
        findings.append(
            Finding(
                code="REPLAYED_ATTEMPT_ID",
                from_stage=Stage.PAYMENT_ATTEMPT,
                to_stage=Stage.PAYMENT_ATTEMPT,
                severity="high",
                explanation="The same attempt_id appears more than once in the payment-attempt stream.",
            )
        )
    if len(settled_payment_ids) > 1:
        findings.append(
            Finding(
                code="RETRY_DUPLICATE_PAYMENT",
                from_stage=Stage.PAYMENT_ATTEMPT,
                to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                severity="critical",
                explanation="More than one distinct authoritative payment settled for one causal trace.",
            )
        )

    if settled and settled.value == "settled":
        delivered = _latest(events, Stage.RESOURCE_OUTCOME_DELIVERY, "delivery_status")
        if delivered is None or delivered.value not in {"delivered", "complete"}:
            findings.append(
                Finding(
                    code="SETTLED_BUT_NOT_DELIVERED",
                    from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                    severity="critical",
                    explanation="Authoritative settlement exists without confirmed resource/outcome delivery.",
                )
            )

    delivered = _latest(events, Stage.RESOURCE_OUTCOME_DELIVERY, "delivery_status")
    if delivered and delivered.value in {"delivered", "complete"}:
        if settled is None or settled.value != "settled":
            findings.append(
                Finding(
                    code="DELIVERED_BUT_NOT_SETTLED",
                    from_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                    to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    severity="high",
                    explanation="Delivery is confirmed but authoritative settlement finality is absent.",
                )
            )

    receipt = _latest(events, Stage.RECEIPT, "payment_status")
    if receipt and settled and receipt.value != settled.value:
        findings.append(
            Finding(
                code="RECEIPT_FINALITY_MISMATCH",
                from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                to_stage=Stage.RECEIPT,
                severity="high",
                explanation=(
                    f"Receipt says {receipt.value!r} while authoritative finality says {settled.value!r}."
                ),
            )
        )

    if settled and delivered:
        reconciliation = _latest(events, Stage.RECONCILIATION, "status")
        if reconciliation is None:
            findings.append(
                Finding(
                    code="RECONCILIATION_GAP",
                    from_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation="Settlement and delivery are present, but no terminal reconciliation record exists.",
                )
            )

    return findings
