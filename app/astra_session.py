from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .astra_origin import normalize_origin
from .astra_verifier import Finding, Stage, StateEvent


_ALLOWED_DIMENSIONS = (
    "user_id",
    "agent_id",
    "payment_instrument_id",
    "merchant_origin",
    "operation_id",
)

_DIMENSION_FINDINGS = {
    "user_id": ("SESSION_USER_CROSSOVER", "critical", "user"),
    "agent_id": ("SESSION_AGENT_CROSSOVER", "high", "agent/runtime principal"),
    "payment_instrument_id": (
        "SESSION_INSTRUMENT_CROSSOVER",
        "critical",
        "payment instrument",
    ),
    "merchant_origin": ("SESSION_MERCHANT_CROSSOVER", "high", "merchant origin"),
    "operation_id": ("SESSION_OPERATION_CROSSOVER", "high", "business operation"),
}


@dataclass(frozen=True)
class _Contract:
    event: StateEvent
    dimensions: tuple[str, ...]


@dataclass(frozen=True)
class _PrincipalValue:
    status: str
    value: str | None = None


def _contract(value: Any) -> tuple[bool, tuple[str, ...], bool]:
    if value is True:
        return True, _ALLOWED_DIMENSIONS, True
    if not isinstance(value, Mapping):
        return False, (), False

    required = value.get("required") is True
    raw_dimensions = value.get("dimensions")
    if not required or not isinstance(raw_dimensions, (list, tuple)):
        return required, (), False

    dimensions: list[str] = []
    for raw in raw_dimensions:
        if not isinstance(raw, str) or raw not in _ALLOWED_DIMENSIONS:
            return required, (), False
        if raw not in dimensions:
            dimensions.append(raw)
    return required, tuple(dimensions), bool(dimensions)


def _mapping(event: StateEvent) -> Mapping[str, Any]:
    return event.value if isinstance(event.value, Mapping) else {}


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)):
        normalized = str(value).strip()
        return normalized or None
    return None


def _principal_value(event: StateEvent, dimension: str) -> _PrincipalValue:
    values = _mapping(event)

    if dimension == "operation_id":
        mapped = _text(values.get("operation_id"))
        field = _text(event.operation_id)
        if mapped is not None and field is not None and mapped != field:
            return _PrincipalValue("invalid")
        value = mapped or field
    else:
        value = _text(values.get(dimension))

    if value is None:
        return _PrincipalValue("missing")
    if dimension == "merchant_origin":
        origin = normalize_origin(value)
        if origin is None:
            return _PrincipalValue("invalid")
        return _PrincipalValue("valid", origin)
    return _PrincipalValue("valid", value)


def _principal_set(
    event: StateEvent,
    dimensions: tuple[str, ...],
) -> tuple[dict[str, str], tuple[str, ...]]:
    values: dict[str, str] = {}
    unresolved: list[str] = []
    for dimension in dimensions:
        principal = _principal_value(event, dimension)
        if principal.status != "valid" or principal.value is None:
            unresolved.append(dimension)
            continue
        values[dimension] = principal.value
    return values, tuple(unresolved)


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


def _session_events(
    events: list[StateEvent],
    key: str,
    session_id: str,
) -> list[StateEvent]:
    return [
        event
        for event in events
        if event.key == key and event.session_id == session_id
    ]


def verify_payment_session_principal_binding(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify a payment session against its intended financial principals.

    An integration opts in with ``requires_payment_session_principal_binding``
    and lists the dimensions it claims are fixed for the session. An
    authoritative ``payment_session_binding`` event provides the expected
    principals, while ``payment_session_use`` records the principals observed at
    a real payment attempt.

    This verifier proves attempt-context divergence only. It does not infer that
    a backend accepted the crossed use or that settlement occurred; those
    stronger conclusions require separate authoritative finality evidence.
    """

    materialized = list(events)
    findings: list[Finding] = []

    raw_declarations = [
        event
        for event in materialized
        if event.stage in {Stage.POLICY_DECISION, Stage.MANDATE_AUTHORIZATION}
        and event.key == "requires_payment_session_principal_binding"
    ]

    contracts: dict[str | None, _Contract] = {}
    for declaration in raw_declarations:
        required, dimensions, valid = _contract(declaration.value)
        if not required:
            continue
        if not valid:
            findings.append(
                _finding(
                    code="PAYMENT_SESSION_CONTRACT_INVALID",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="medium",
                    explanation=(
                        "The payment-session binding contract does not declare a "
                        "non-empty supported dimensions list."
                    ),
                    operation_id=declaration.operation_id,
                    evidence=[declaration],
                )
            )
            continue
        contracts[declaration.session_id] = _Contract(declaration, dimensions)

    if not contracts:
        return findings

    binding_events = [
        event
        for event in materialized
        if event.stage in {Stage.POLICY_DECISION, Stage.MANDATE_AUTHORIZATION}
        and event.key == "payment_session_binding"
    ]
    use_events = [
        event
        for event in materialized
        if event.stage == Stage.PAYMENT_ATTEMPT
        and event.key == "payment_session_use"
    ]

    global_contract = contracts.get(None)
    unscoped_uses = [event for event in use_events if event.session_id is None]
    if global_contract and unscoped_uses:
        findings.append(
            _finding(
                code="SESSION_USE_BINDING_UNRESOLVED",
                from_stage=Stage.POLICY_DECISION,
                to_stage=Stage.PAYMENT_ATTEMPT,
                severity="medium",
                explanation=(
                    "One or more payment-session use events omit session_id, so "
                    "the intended session binding cannot be selected."
                ),
                operation_id=None,
                evidence=[global_contract.event, *unscoped_uses],
            )
        )

    session_ids = {
        event.session_id
        for event in [*binding_events, *use_events]
        if event.session_id is not None
    }
    session_ids.update(session_id for session_id in contracts if session_id is not None)

    if global_contract and not session_ids:
        findings.append(
            _finding(
                code="PAYMENT_SESSION_BINDING_MISSING",
                from_stage=Stage.POLICY_DECISION,
                to_stage=Stage.PAYMENT_ATTEMPT,
                severity="medium",
                explanation=(
                    "Session-principal verification is required, but the trace "
                    "contains no identifiable payment session."
                ),
                operation_id=global_contract.event.operation_id,
                evidence=[global_contract.event],
            )
        )
        return findings

    for session_id in sorted(session_ids):
        contract = contracts.get(session_id) or global_contract
        if contract is None:
            continue

        candidates = [
            event
            for event in _session_events(
                binding_events,
                "payment_session_binding",
                session_id,
            )
            if event.authoritative
        ]
        if not candidates:
            findings.append(
                _finding(
                    code="PAYMENT_SESSION_BINDING_MISSING",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="medium",
                    explanation=(
                        f"Session {session_id!r} has no authoritative principal "
                        "binding in the supplied trace."
                    ),
                    operation_id=contract.event.operation_id,
                    evidence=[contract.event],
                )
            )
            continue

        complete_bindings: list[tuple[StateEvent, dict[str, str]]] = []
        for binding in candidates:
            values, unresolved = _principal_set(binding, contract.dimensions)
            if unresolved:
                findings.append(
                    _finding(
                        code="PAYMENT_SESSION_BINDING_INCOMPLETE",
                        from_stage=Stage.POLICY_DECISION,
                        to_stage=Stage.PAYMENT_ATTEMPT,
                        severity="medium",
                        explanation=(
                            f"Session {session_id!r} binding lacks valid values "
                            f"for required dimension(s) {list(unresolved)!r}."
                        ),
                        operation_id=binding.operation_id,
                        evidence=[contract.event, binding],
                    )
                )
                continue
            complete_bindings.append((binding, values))

        if not complete_bindings:
            continue

        fingerprints = {
            tuple((dimension, values[dimension]) for dimension in contract.dimensions)
            for _, values in complete_bindings
        }
        if len(fingerprints) > 1:
            findings.append(
                _finding(
                    code="PAYMENT_SESSION_BINDING_CONFLICT",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="high",
                    explanation=(
                        f"Session {session_id!r} has conflicting authoritative "
                        "principal bindings."
                    ),
                    operation_id=None,
                    evidence=[contract.event, *(item[0] for item in complete_bindings)],
                )
            )
            continue

        binding, expected = complete_bindings[-1]
        uses = _session_events(use_events, "payment_session_use", session_id)
        if not uses:
            findings.append(
                _finding(
                    code="PAYMENT_SESSION_USE_EVIDENCE_MISSING",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="medium",
                    explanation=(
                        f"Session {session_id!r} has an authoritative binding but "
                        "no observed payment-session use event."
                    ),
                    operation_id=binding.operation_id,
                    evidence=[contract.event, binding],
                )
            )
            continue

        resolved_operations: set[str] = set()
        for use in uses:
            observed, unresolved = _principal_set(use, contract.dimensions)
            if unresolved:
                findings.append(
                    _finding(
                        code="SESSION_USE_BINDING_UNRESOLVED",
                        from_stage=Stage.POLICY_DECISION,
                        to_stage=Stage.PAYMENT_ATTEMPT,
                        severity="medium",
                        explanation=(
                            f"Session {session_id!r} use lacks valid values for "
                            f"required dimension(s) {list(unresolved)!r}."
                        ),
                        operation_id=use.operation_id,
                        evidence=[contract.event, binding, use],
                    )
                )

            for dimension in contract.dimensions:
                if dimension not in observed:
                    continue
                if observed[dimension] == expected[dimension]:
                    continue
                code, severity, label = _DIMENSION_FINDINGS[dimension]
                findings.append(
                    _finding(
                        code=code,
                        from_stage=Stage.POLICY_DECISION,
                        to_stage=Stage.PAYMENT_ATTEMPT,
                        severity=severity,
                        explanation=(
                            f"Session {session_id!r} is bound to {label} "
                            f"{expected[dimension]!r}, but the payment attempt "
                            f"used {observed[dimension]!r}."
                        ),
                        operation_id=use.operation_id,
                        evidence=[contract.event, binding, use],
                    )
                )

            operation = observed.get("operation_id")
            if operation is not None:
                resolved_operations.add(operation)

        if "operation_id" in contract.dimensions and len(resolved_operations) > 1:
            findings.append(
                _finding(
                    code="SESSION_ID_REUSED_ACROSS_OPERATIONS",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="high",
                    explanation=(
                        f"Operation-scoped session {session_id!r} was used across "
                        f"multiple business operations {sorted(resolved_operations)!r}."
                    ),
                    operation_id=None,
                    evidence=[contract.event, binding, *uses],
                )
            )

    return findings


__all__ = ["verify_payment_session_principal_binding"]
