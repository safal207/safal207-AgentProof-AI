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

_NO_RECOVERY_ACTIONS = {
    "already_used",
    "complete",
    "completed",
    "done",
    "none",
    "no_action",
    "stop",
    "token_already_used",
}


def _status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _required(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, Mapping):
        return value.get("required") is True
    return False


def _scope(events: list[StateEvent], operation_id: str | None) -> list[StateEvent]:
    if operation_id is None:
        return [event for event in events if event.operation_id is None]
    return [event for event in events if event.operation_id == operation_id]


def _terminal_commitment(event: StateEvent) -> tuple[str, str] | None:
    if event.key != "terminal_commitment" or not isinstance(event.value, Mapping):
        return None
    if event.value.get("terminal") is not True:
        return None

    component = event.value.get("component")
    status = event.value.get("status")
    if not isinstance(component, str) or not component.strip():
        return None
    normalized_status = _status(status)
    if normalized_status is None:
        return None
    return component.strip(), normalized_status


def _latest(
    events: list[StateEvent],
    stages: set[Stage],
    keys: set[str],
) -> StateEvent | None:
    matched = [event for event in events if event.stage in stages and event.key in keys]
    return matched[-1] if matched else None


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
    sources = tuple(
        dict.fromkeys(
            event.source
            for event in evidence
            if event is not None and event.source
        )
    )
    return Finding(
        code=code,
        from_stage=from_stage,
        to_stage=to_stage,
        severity=severity,
        explanation=explanation,
        evidence_sources=sources,
        operation_id=operation_id,
    )


def verify_terminal_commitment_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify that terminal local commitment does not outrun settlement finality.

    Adapters opt in with a ``requires_settlement_before_terminal_commitment``
    event at QUOTE/CHALLENGE or MANDATE/AUTHORIZATION. They then normalize
    local token/order/credential state as ``terminal_commitment`` events whose
    value contains ``component``, ``status``, and ``terminal: true``.

    A local store can be authoritative about its own state without being
    authoritative about payment finality. This verifier keeps those two claims
    separate and reasons only about the supplied trace.
    """

    materialized = list(events)
    declarations: dict[str | None, StateEvent] = {}
    for event in materialized:
        if event.stage not in {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION}:
            continue
        if event.key != "requires_settlement_before_terminal_commitment":
            continue
        if _required(event.value):
            declarations[event.operation_id] = event

    findings: list[Finding] = []
    for operation_id, declaration in declarations.items():
        scoped = _scope(materialized, operation_id)
        indexed = list(enumerate(scoped))

        commitments = [
            (index, event, parsed)
            for index, event in indexed
            if (parsed := _terminal_commitment(event)) is not None
        ]
        if not commitments:
            continue

        settled = [
            (index, event)
            for index, event in indexed
            if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
            and event.key == "payment_status"
            and event.authoritative
            and _status(event.value) in _SETTLED_STATUSES
        ]
        first_settled = settled[0] if settled else None
        commitment_events = [event for _, event, _ in commitments]
        components = sorted({parsed[0] for _, _, parsed in commitments})

        if first_settled is None:
            findings.append(
                _finding(
                    code="TERMINAL_COMMITMENT_WITHOUT_SETTLEMENT",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    severity="high",
                    explanation=(
                        f"Terminal local state for components {components!r} is present "
                        "without authoritative settlement completion."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, *commitment_events],
                )
            )

            next_action = _latest(
                scoped,
                {Stage.CLAIMED_RESULT, Stage.RECONCILIATION},
                {"next_action"},
            )
            if next_action and _status(next_action.value) in _NO_RECOVERY_ACTIONS:
                findings.append(
                    _finding(
                        code="NONSETTLED_OPERATION_MARKED_NONRETRYABLE",
                        from_stage=Stage.CLAIMED_RESULT,
                        to_stage=Stage.RECONCILIATION,
                        severity="high",
                        explanation=(
                            f"The trace reports next_action={next_action.value!r} while "
                            "authoritative settlement completion is absent."
                        ),
                        operation_id=operation_id,
                        evidence=[next_action, declaration, *commitment_events],
                    )
                )
            continue

        settled_index, settled_event = first_settled
        premature = [
            event
            for index, event, _ in commitments
            if index < settled_index
        ]
        if premature:
            findings.append(
                _finding(
                    code="TERMINAL_COMMITMENT_PRECEDES_SETTLEMENT",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    severity="high",
                    explanation=(
                        "Terminal local state was persisted before authoritative "
                        "settlement completion, leaving an interruption window."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, *premature, settled_event],
                )
            )

    return findings
