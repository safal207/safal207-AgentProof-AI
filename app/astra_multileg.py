from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .astra_verifier import Finding, Stage, StateEvent


_COMPLETED_LEG_STATUSES = {
    "captured",
    "complete",
    "completed",
    "confirmed",
    "final",
    "finalized",
    "funded",
    "paid",
    "settled",
    "success",
    "succeeded",
}

_COMPLETE_CLAIM_STATUSES = {
    "complete",
    "completed",
    "confirmed",
    "paid",
    "payment_confirmed",
    "settled",
    "success",
    "succeeded",
}

_NO_RECOVERY_ACTIONS = {
    "complete",
    "completed",
    "done",
    "none",
    "no_action",
    "stop",
}


def _status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _required_legs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return ()
        leg = item.strip().lower().replace("-", "_").replace(" ", "_")
        if leg not in normalized:
            normalized.append(leg)
    return tuple(normalized)


def _leg_status(event: StateEvent) -> tuple[str, str] | None:
    if event.stage != Stage.ACTUAL_SETTLEMENT_FINALITY:
        return None

    if event.key == "settlement_leg_status" and isinstance(event.value, Mapping):
        raw_leg = event.value.get("leg")
        raw_status = event.value.get("status")
        if not isinstance(raw_leg, str) or not raw_leg.strip():
            return None
        status = _status(raw_status)
        if status is None:
            return None
        leg = raw_leg.strip().lower().replace("-", "_").replace(" ", "_")
        return leg, status

    suffix = "_leg_status"
    if event.key.endswith(suffix):
        leg = event.key[: -len(suffix)].strip().lower()
        status = _status(event.value)
        if leg and status is not None:
            return leg, status

    return None


def _scope(events: list[StateEvent], operation_id: str | None) -> list[StateEvent]:
    if operation_id is None:
        return [event for event in events if event.operation_id is None]
    return [event for event in events if event.operation_id == operation_id]


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


def verify_multileg_causal_outcome(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify multi-leg payment completion without collapsing intermediate funding.

    A caller declares the required economic legs with a
    ``required_settlement_legs`` event at QUOTE/CHALLENGE or
    MANDATE/AUTHORIZATION. Authoritative leg evidence is represented either as
    ``settlement_leg_status`` with ``{"leg": ..., "status": ...}`` or as a
    protocol-specific ``<leg>_leg_status`` key.

    The function only reasons about the supplied trace. A missing leg means no
    authoritative completion evidence for that leg is present in the trace; it
    does not prove the event never happened elsewhere.
    """

    materialized = list(events)
    declarations: dict[str | None, StateEvent] = {}
    for event in materialized:
        if event.stage not in {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION}:
            continue
        if event.key not in {"required_settlement_legs", "required_payment_legs"}:
            continue
        if _required_legs(event.value):
            declarations[event.operation_id] = event

    findings: list[Finding] = []
    for operation_id, declaration in declarations.items():
        required = _required_legs(declaration.value)
        scoped = _scope(materialized, operation_id)

        latest_leg_events: dict[str, tuple[StateEvent, str]] = {}
        for event in scoped:
            parsed = _leg_status(event)
            if parsed is None:
                continue
            leg, status = parsed
            if leg in required:
                latest_leg_events[leg] = (event, status)

        completed = {
            leg
            for leg, (event, status) in latest_leg_events.items()
            if event.authoritative and status in _COMPLETED_LEG_STATUSES
        }
        missing = tuple(leg for leg in required if leg not in completed)
        if not completed or not missing:
            continue

        completed_evidence = [latest_leg_events[leg][0] for leg in required if leg in completed]
        evidence: list[StateEvent | None] = [declaration, *completed_evidence]

        funding_complete = "funding" in completed
        merchant_missing = "merchant" in missing
        if funding_complete and merchant_missing:
            findings.append(
                _finding(
                    code="FUNDED_BUT_MERCHANT_UNSETTLED",
                    from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    to_stage=Stage.RESOURCE_OUTCOME_DELIVERY,
                    severity="critical",
                    explanation=(
                        "The funding leg has authoritative completion evidence, "
                        "but merchant-settlement evidence is absent from the supplied trace."
                    ),
                    operation_id=operation_id,
                    evidence=evidence,
                )
            )
        else:
            findings.append(
                _finding(
                    code="PARTIAL_SETTLEMENT_OUTCOME_UNRESOLVED",
                    from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        f"Completed legs {sorted(completed)!r}; required legs "
                        f"{list(required)!r}; missing completion evidence for {list(missing)!r}."
                    ),
                    operation_id=operation_id,
                    evidence=evidence,
                )
            )

        claim = _latest(
            scoped,
            {Stage.CLAIMED_RESULT},
            {"payment_status", "payment_phase", "outcome_status"},
        )
        if claim and _status(claim.value) in _COMPLETE_CLAIM_STATUSES:
            findings.append(
                _finding(
                    code="PARTIAL_SETTLEMENT_CLAIMED_COMPLETE",
                    from_stage=Stage.CLAIMED_RESULT,
                    to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    severity="critical",
                    explanation=(
                        f"The outcome is claimed complete while required legs "
                        f"{list(missing)!r} lack authoritative completion evidence."
                    ),
                    operation_id=operation_id,
                    evidence=[claim, *evidence],
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
                    code="RECOVERY_ACTION_MISSING",
                    from_stage=Stage.CLAIMED_RESULT,
                    to_stage=Stage.RECONCILIATION,
                    severity="high",
                    explanation=(
                        f"The trace explicitly reports next_action={next_action.value!r} "
                        f"while required legs {list(missing)!r} remain unresolved."
                    ),
                    operation_id=operation_id,
                    evidence=[next_action, *evidence],
                )
            )

    return findings
