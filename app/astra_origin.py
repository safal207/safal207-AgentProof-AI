from __future__ import annotations

import ipaddress
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from .astra_verifier import Finding, Stage, StateEvent


_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_IDENTITY_FIELDS = ("authorization_id", "payment_id")


def normalize_origin(value: Any) -> str | None:
    """Return a stable HTTP(S) origin or ``None`` for unsafe input.

    Paths, queries, and fragments are intentionally discarded. Scheme and host
    case, IDNA hostnames, IP literals, and default ports are normalized.
    Userinfo, non-HTTP schemes, and malformed ports are rejected because they
    are not valid payment-credential network principals for this contract.
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
    if (
        scheme not in _ALLOWED_SCHEMES
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
        is_ipv6 = False
    else:
        host = ip.compressed.lower()
        is_ipv6 = ip.version == 6

    if not host:
        return None

    serialized_host = f"[{host}]" if is_ipv6 else host
    default_port = _DEFAULT_PORTS[scheme]
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
    matched = [
        event
        for event in events
        if event.stage == stage and event.key == key
    ]
    return matched[-1] if matched else None


def _has_identity(event: StateEvent | None) -> bool:
    return bool(
        event is not None
        and any(getattr(event, field) is not None for field in _IDENTITY_FIELDS)
    )


def _delegate_applies(
    delegate: StateEvent,
    target: StateEvent | None,
) -> bool:
    """Match delegate scope without conflating identity namespaces.

    An unscoped delegate applies to the whole operation. A scoped delegate
    applies only when every identity field it declares is present with the same
    value on the target event. Equal strings in ``authorization_id`` and
    ``payment_id`` are deliberately not interchangeable.
    """

    scoped = False
    for field in _IDENTITY_FIELDS:
        expected = getattr(delegate, field)
        if expected is None:
            continue
        scoped = True
        if target is None or getattr(target, field) != expected:
            return False
    return True if scoped else True


def _delegate_origins(
    delegate_events: list[StateEvent],
    target: StateEvent | None,
) -> set[str]:
    """Return global delegates plus delegates scoped to the target identity."""

    origins: set[str] = set()
    for event in delegate_events:
        if not _delegate_applies(event, target):
            continue
        normalized = normalize_origin(event.value)
        if normalized is not None:
            origins.add(normalized)
    return origins


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
    """Verify challenge, credential-dispatch, and consumer origins.

    Adapters opt in with ``requires_credential_origin_binding``. They then
    provide ``challenge_origin``, ``credential_bound_origin``, and one
    ``credential_dispatch_origin`` event for each observed recipient. An
    authenticated intermediary is admitted only through an authoritative
    ``authorized_credential_delegate_origin`` event. Delegate evidence may be
    operation-global or scoped to an ``authorization_id``/``payment_id``.

    Dispatch exposure is not settlement evidence. The stronger wrong-consumer
    finding requires an authoritative ``credential_consumer_origin`` event.
    """

    materialized = list(events)
    declarations: dict[str | None, StateEvent] = {}
    for event in materialized:
        if event.stage not in {
            Stage.QUOTE_CHALLENGE,
            Stage.MANDATE_AUTHORIZATION,
        }:
            continue
        if (
            event.key == "requires_credential_origin_binding"
            and _contract_required(event.value)
        ):
            declarations[event.operation_id] = event

    findings: list[Finding] = []
    for operation_id, declaration in declarations.items():
        scoped = _scope(materialized, operation_id)
        challenge = _latest(
            scoped,
            Stage.QUOTE_CHALLENGE,
            "challenge_origin",
        )
        bound = _latest(
            scoped,
            Stage.MANDATE_AUTHORIZATION,
            "credential_bound_origin",
        )
        challenge_origin = normalize_origin(challenge.value) if challenge else None
        bound_origin = normalize_origin(bound.value) if bound else None

        if challenge_origin is None or bound_origin is None:
            findings.append(
                _finding(
                    code="CREDENTIAL_ORIGIN_EVIDENCE_MISSING",
                    from_stage=Stage.QUOTE_CHALLENGE,
                    to_stage=Stage.MANDATE_AUTHORIZATION,
                    severity="medium",
                    explanation=(
                        "The trace does not expose valid, comparable challenge "
                        "and credential-bound HTTP(S) origins."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound],
                )
            )
            continue

        delegate_events = [
            event
            for event in scoped
            if event.stage == Stage.MANDATE_AUTHORIZATION
            and event.key == "authorized_credential_delegate_origin"
            and event.authoritative
        ]
        bound_allowed = {
            challenge_origin,
            *_delegate_origins(delegate_events, bound),
        }

        if bound_origin not in bound_allowed:
            findings.append(
                _finding(
                    code="AUTHORIZATION_ORIGIN_DIVERGENCE",
                    from_stage=Stage.QUOTE_CHALLENGE,
                    to_stage=Stage.MANDATE_AUTHORIZATION,
                    severity="high",
                    explanation=(
                        f"Credential binding origin {bound_origin!r} does not "
                        f"match challenge origin {challenge_origin!r} or an "
                        "authenticated delegate for that payment identity."
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
        if not dispatches:
            findings.append(
                _finding(
                    code="CREDENTIAL_DISPATCH_EVIDENCE_MISSING",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="medium",
                    explanation=(
                        "Credential-origin verification is required, but the "
                        "trace identifies no recipient of the reusable credential."
                    ),
                    operation_id=operation_id,
                    evidence=[declaration, challenge, bound],
                )
            )

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
                    explanation=(
                        "One or more credential dispatch destinations are not "
                        "valid HTTP(S) origins."
                    ),
                    operation_id=operation_id,
                    evidence=[
                        declaration,
                        challenge,
                        bound,
                        *invalid_dispatches,
                    ],
                )
            )

        identity_missing = [
            event
            for event, _ in normalized_dispatches
            if not _has_identity(event)
        ]
        if identity_missing:
            findings.append(
                _finding(
                    code="CREDENTIAL_IDENTITY_EVIDENCE_MISSING",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="medium",
                    explanation=(
                        "One or more dispatch events omit both authorization_id "
                        "and payment_id, so credential reuse cannot be excluded."
                    ),
                    operation_id=operation_id,
                    evidence=[
                        declaration,
                        challenge,
                        bound,
                        *identity_missing,
                    ],
                )
            )

        unauthorized_pairs: list[tuple[StateEvent, str]] = []
        for event, origin in normalized_dispatches:
            allowed = {
                challenge_origin,
                *_delegate_origins(delegate_events, event),
            }
            if origin not in allowed:
                unauthorized_pairs.append((event, origin))

        if unauthorized_pairs:
            recipients = sorted({origin for _, origin in unauthorized_pairs})
            findings.append(
                _finding(
                    code="PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="high",
                    explanation=(
                        "A reusable payment credential was dispatched to "
                        f"unauthorized origin(s) {recipients!r}; accepted "
                        f"challenge origin is {challenge_origin!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[
                        declaration,
                        challenge,
                        bound,
                        *(event for event, _ in unauthorized_pairs),
                    ],
                )
            )

        by_identity: dict[
            tuple[str, str],
            list[tuple[StateEvent, str]],
        ] = defaultdict(list)
        for event, origin in normalized_dispatches:
            for field in _IDENTITY_FIELDS:
                value = getattr(event, field)
                if value is not None:
                    by_identity[(field, value)].append((event, origin))

        reused_keys: list[str] = []
        reused_events: list[StateEvent] = []
        reused_origins: set[str] = set()
        for (field, value), group in by_identity.items():
            origins = {origin for _, origin in group}
            if len(origins) <= 1:
                continue
            reused_keys.append(f"{field}={value!r}")
            reused_origins.update(origins)
            for event, _ in group:
                if event not in reused_events:
                    reused_events.append(event)

        if reused_keys:
            findings.append(
                _finding(
                    code="CROSS_ORIGIN_CREDENTIAL_REUSE",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.PAYMENT_ATTEMPT,
                    severity="high",
                    explanation=(
                        f"Payment identity key(s) {sorted(reused_keys)!r} were "
                        f"dispatched to multiple origins {sorted(reused_origins)!r}, "
                        "creating a cross-origin consumption race."
                    ),
                    operation_id=operation_id,
                    evidence=[
                        declaration,
                        challenge,
                        bound,
                        *reused_events,
                    ],
                )
            )

        consumers = [
            event
            for event in scoped
            if event.stage == Stage.ACTUAL_SETTLEMENT_FINALITY
            and event.key == "credential_consumer_origin"
            and event.authoritative
        ]
        unauthorized_consumers: list[StateEvent] = []
        for event in consumers:
            origin = normalize_origin(event.value)
            allowed = {
                challenge_origin,
                *_delegate_origins(delegate_events, event),
            }
            if origin is None or origin not in allowed:
                unauthorized_consumers.append(event)

        if unauthorized_consumers:
            findings.append(
                _finding(
                    code="SETTLEMENT_CONSUMER_ORIGIN_DIVERGENCE",
                    from_stage=Stage.PAYMENT_ATTEMPT,
                    to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                    severity="critical",
                    explanation=(
                        "Authoritative settlement evidence attributes credential "
                        "consumption to an origin outside the accepted challenge "
                        "or authenticated-delegation boundary."
                    ),
                    operation_id=operation_id,
                    evidence=[
                        declaration,
                        challenge,
                        bound,
                        *unauthorized_consumers,
                    ],
                )
            )

    return findings


__all__ = ["normalize_origin", "verify_payment_credential_origin"]
