from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from .astra_verifier import Finding, Stage, StateEvent


_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(value: Any) -> str | None:
    """Return a stable URL origin or ``None`` for malformed/non-origin input.

    The comparison intentionally drops path, query, and fragment; normalizes
    scheme/host case and IDNA; and removes default HTTP(S) ports. Userinfo is
    rejected because it is not part of a trustworthy network-principal origin.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if not scheme or hostname is None or parsed.username is not None or parsed.password is not None:
        return None

    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not host:
        return None

    # urlsplit removes IPv6 brackets from .hostname; restore them in the
    # serialized origin while leaving ordinary DNS names untouched.
    serialized_host = f"[{host}]" if ":" in host else host
    default_port = _DEFAULT_PORTS.get(scheme)
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{serialized_host}{port_suffix}"


def _contract_required(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, Mapping) and value.get("required") is True


def _scope(events: list[StateEvent], operation_id: str | None) -> list[StateEvent]:
    if operation_id is None:
        return [event for event in events if event.operation_id is None]
    return [event for event in events if event.operation_id == operation_id]


def _latest(events: list[StateEvent], stage: Stage, key: str) -> StateEvent | None:
    matched = [event for event in events if event.stage == stage and event.key == key]
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


def verify_payment_credential_origin(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify challenge, credential dispatch, and settlement-consumer origins.

    Protocol adapters opt in with ``requires_credential_origin_binding`` at
    QUOTE/CHALLENGE or MANDATE/AUTHORIZATION. The accepted challenge origin is
    supplied as ``challenge_origin`` and the payment identity's intended
    recipient as ``credential_bound_origin``. Actual credential recipients are
    emitted as ``credential_dispatch_origin`` events.

    An independently authenticated delegate may be added with an authoritative
    ``authorized_credential_delegate_origin`` event. Merely observing a
    redirect does not authorize its origin to receive a reusable credential.
    """

    materialized = list(events)
    declarations: dict[str | None, StateEvent] = {}
    for event in materialized:
        if event.stage not in {Stage.QUOTE_CHALLENGE, Stage.MANDATE_AUTHORIZATION}:
            continue
        if event.key == "requires_credential_origin_binding" and _contract_required(event.value):
            declarations[event.operation_id] = event

    findings: list[Finding] = []
    for operation_id, declaration in declarations.items():
        scoped = _scope(materialized, operation_id)
        challenge = _latest(scoped, Stage.QUOTE_CHALLENGE, "challenge_origin")
        bound = _latest(scoped, Stage.MANDATE_AUTHORIZATION, "credential_bound_origin")
        challenge_origin = normalize_origin(challenge.value) if challenge else None
        bound_origin = normalize_origin(bound.value) if bound else None

        malformed_evidence = [
            event
            for event, normalized in ((challenge, challenge_origin), (bound, bound_origin))
            if event is None or normalized is None
        ]
        if malformed_evidence:
            findings.append(
                _finding(
                    code="CREDENTIAL_ORIGIN_EVIDENCE_MISSING",
                    from_stage=Stage.QUOTE_CHALLENGE,
                    to_stage=Stage.MANDATE_AUTHORIZATION,
                    severity="medium",
                    explanation=(
                        "The trace does not expose valid, comparable challenge and "
                        "credential-bound URL origins."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound],
                )
            )
            # Without both principals, downstream origin comparisons would
            # overstate certainty. Keep only the evidence-gap finding.
            continue

        delegate_events = [
            event
            for event in scoped
            if event.stage == Stage.MANDATE_AUTHORIZATION
            and event.key == "authorized_credential_delegate_origin"
            and event.authoritative
        ]
        delegate_origins = {
            normalized
            for event in delegate_events
            if (normalized := normalize_origin(event.value)) is not None
        }
        allowed_origins = {challenge_origin, *delegate_origins}

        if bound_origin not in allowed_origins:
            findings.append(
                _finding(
                    code="AUTHORIZATION_ORIGIN_DIVERGENCE",
                    from_stage=Stage.QUOTE_CHALLENGE,
                    to_stage=Stage.MANDATE_AUTHORIZATION,
                    severity="high",
                    explanation=(
                        f"Credential binding origin {bound_origin!r} does not match "
                        f"challenge origin {challenge_origin!r} or an authenticated delegate."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound, *delegate_events],
                )
            )

        dispatches = [
            event
            for event in scoped
            if event.stage == Stage.PAYMENT_ATTEMPT
            and event.key == "credential_dispatch_origin"
        ]
        normalized_dispatches: list[tuple[StateEvent, str]] = []
        invalid_dispatches: list[StateEvent] = []
        for event in dispatches:
            origin = normalize_origin(event.value)
            if origin is None:
                invalid_dispatches.append(event)
            else:
                normalized_dispatches.append((event, origin))

        if invalid_dispatches:
            findings.append(
                _finding(
                    code="CREDENTIAL_DISPATCH_ORIGIN_INVALID",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="medium",
                    explanation="One or more credential dispatch destinations are not valid URL origins.",
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound, *invalid_dispatches],
                )
            )

        unauthorized_dispatches = [
            event
            for event, origin in normalized_dispatches
            if origin not in allowed_origins
        ]
        if unauthorized_dispatches:
            recipients = sorted(
                {
                    origin
                    for event, origin in normalized_dispatches
                    if event in unauthorized_dispatches
                }
            )
            findings.append(
                _finding(
                    code="PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="high",
                    explanation=(
                        f"A reusable payment credential was dispatched to unauthorized "
                        f"origin(s) {recipients!r}; accepted challenge origin is "
                        f"{challenge_origin!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound, *unauthorized_dispatches],
                )
            )

        by_authorization: dict[str, list[tuple[StateEvent, str]]] = defaultdict(list)
        for event, origin in normalized_dispatches:
            identity = event.authorization_id or event.payment_id
            if identity:
                by_authorization[identity].append((event, origin))
        for identity, group in by_authorization.items():
            origins = {origin for _, origin in group}
            if len(origins) <= 1:
                continue
            findings.append(
                _finding(
                    code="CROSS_ORIGIN_CREDENTIAL_REUSE",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="high",
                    explanation=(
                        f"Payment authorization {identity!r} was dispatched to multiple "
                        f"origins {sorted(origins)!r}, creating a cross-origin consumption race."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound, *(event for event, _ in group)],
                )
            )

        consumers = [
            event
            for event in scoped
            if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
            and event.key == "credential_consumer_origin"
            and event.authoritative
        ]
        unauthorized_consumers = [
            event
            for event in consumers
            if (origin := normalize_origin(event.value)) is None or origin not in allowed_origins
        ]
        if unauthorized_consumers:
            findings.append(
                _finding(
                    code="SETTLEMENT_CONSUMER_ORIGIN_DIVERGENCE",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    severity="critical",
                    explanation=(
                        "Authoritative settlement evidence attributes credential consumption "
                        "to an origin outside the accepted challenge/delegation boundary."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound, *unauthorized_consumers],
                )
            )

    return findings


__all__ = ["normalize_origin", "verify_payment_credential_origin"]
