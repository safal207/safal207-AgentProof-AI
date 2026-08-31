from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .astra_verifier import Finding, Stage, StateEvent


_SUPPORTED_DEBIT_BASES = {
    "actual_settlement",
    "authorized_ceiling",
    "explicit_provider_amount",
}
_SUPPORTED_REMAINDER_POLICIES = {
    "credited",
    "external_reconciliation",
    "not_credited",
}
_IDENTITY_FIELDS = ("authorization_id", "payment_id")
_SESSION_ACCOUNTING_KEYS = {
    "claimed_session_remaining_minor",
    "claimed_session_spend_minor",
    "provider_debit_amount_minor",
    "session_credit_minor",
    "session_debit_minor",
    "session_remaining_after_minor",
    "session_remaining_before_minor",
    "session_remainder_reconciliation_status",
}
_ACCOUNTING_TRIGGER_KEYS = _SESSION_ACCOUNTING_KEYS | {
    "authorized_ceiling_minor",
    "settled_amount_minor",
}
_COMPLETE_STATUSES = {"complete", "completed", "reconciled", "success", "succeeded"}


@dataclass(frozen=True)
class _Contract:
    events: tuple[StateEvent, ...]
    debit_basis: str
    remainder_policy: str

    @property
    def event(self) -> StateEvent:
        return self.events[-1]


@dataclass(frozen=True)
class _Amount:
    event: StateEvent
    amount: Decimal
    asset: str


@dataclass(frozen=True)
class _Selection:
    record: _Amount | None
    classified_failure: bool = False


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
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


def _asset(value: Any) -> str | None:
    text = _text(value)
    return text.lower() if text is not None else None


def _minor(value: Any) -> Decimal | None:
    """Parse a non-negative integer amount without binary-float arithmetic."""

    if value is None or isinstance(value, (bool, float)):
        return None
    try:
        if isinstance(value, Decimal):
            parsed = value
        elif isinstance(value, int):
            parsed = Decimal(value)
        elif isinstance(value, str):
            parsed = Decimal(value.strip())
        else:
            return None
    except (InvalidOperation, ValueError):
        return None

    if not parsed.is_finite() or parsed < 0:
        return None
    if parsed != parsed.to_integral_value():
        return None
    return parsed


def _amount(event: StateEvent) -> _Amount | None:
    if not isinstance(event.value, Mapping):
        return None
    amount = _minor(event.value.get("amount_minor"))
    asset = _asset(event.value.get("asset"))
    if amount is None or asset is None:
        return None
    return _Amount(event=event, amount=amount, asset=asset)


def _contract(value: Any) -> tuple[bool, str | None, str | None, bool]:
    if not isinstance(value, Mapping):
        return False, None, None, False
    required = value.get("required") is True
    debit_basis = _status(value.get("debit_basis"))
    remainder_policy = _status(value.get("remainder_policy"))
    valid = bool(
        required
        and debit_basis in _SUPPORTED_DEBIT_BASES
        and remainder_policy in _SUPPORTED_REMAINDER_POLICIES
    )
    return required, debit_basis, remainder_policy, valid


def _identity_profile(events: Iterable[StateEvent]) -> tuple[dict[str, str], bool]:
    profile: dict[str, str] = {}
    for event in events:
        for field in _IDENTITY_FIELDS:
            value = getattr(event, field)
            if value is None:
                continue
            existing = profile.get(field)
            if existing is not None and existing != value:
                return profile, False
            profile[field] = value
    return profile, bool(profile)


def _identity_relation(profile: Mapping[str, str], event: StateEvent) -> str:
    """Compare typed identifiers without crossing identity namespaces."""

    if not profile:
        return "unresolved"
    shared = False
    for field, expected in profile.items():
        observed = getattr(event, field)
        if observed is None:
            continue
        shared = True
        if observed != expected:
            return "divergent"
    return "matched" if shared else "unresolved"


def _identity_fingerprint(event: StateEvent) -> tuple[tuple[str, str], ...] | None:
    values = tuple(
        (field, value)
        for field in _IDENTITY_FIELDS
        if (value := getattr(event, field)) is not None
    )
    return values or None


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


def _effective_contract(
    global_contract: _Contract | None,
    session_contract: _Contract | None,
) -> tuple[_Contract | None, bool]:
    if global_contract is None:
        return session_contract, True
    if session_contract is None:
        return global_contract, True
    if (
        global_contract.debit_basis != session_contract.debit_basis
        or global_contract.remainder_policy != session_contract.remainder_policy
    ):
        return None, False
    return (
        _Contract(
            events=(*global_contract.events, *session_contract.events),
            debit_basis=global_contract.debit_basis,
            remainder_policy=global_contract.remainder_policy,
        ),
        True,
    )


def _authoritative_events(
    events: Iterable[StateEvent],
    *,
    key: str,
    stages: set[Stage],
) -> list[StateEvent]:
    return [
        event
        for event in events
        if event.key == key and event.stage in stages and event.authoritative
    ]


def _select_amount(
    events: Iterable[StateEvent],
    *,
    key: str,
    stages: set[Stage],
    session_id: str,
    operation_id: str,
    identity: Mapping[str, str],
    asset: str,
    findings: list[Finding],
    seen: set[tuple[str, str | None]],
    contract: _Contract,
    missing_code: str | None = None,
    missing_explanation: str | None = None,
) -> _Selection:
    """Select one authoritative amount and fail closed on conflicting identity.

    Evidence in another session/operation that shares the payment identity is a
    scope mismatch. Evidence in the same session/operation with a divergent or
    incomparable typed identity is an accounting ambiguity, not absence. If any
    classified conflicting record exists, a simultaneously valid record cannot
    make the boundary clean.
    """

    valid: list[_Amount] = []
    classified = False
    relevant_events: list[StateEvent] = []

    for event in _authoritative_events(events, key=key, stages=stages):
        relation = _identity_relation(identity, event)
        same_scope = (
            event.session_id == session_id and event.operation_id == operation_id
        )

        if relation == "divergent":
            if same_scope:
                classified = True
                relevant_events.append(event)
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_ACCOUNTING_EVIDENCE_CONFLICT",
                        from_stage=Stage.MANDATE_AUTHORIZATION,
                        to_stage=event.stage,
                        severity="high",
                        explanation=(
                            f"Authoritative {key!r} evidence in the same session "
                            "and operation conflicts with the typed payment identity."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events, event],
                    ),
                )
            continue

        if relation == "unresolved":
            if same_scope:
                classified = True
                relevant_events.append(event)
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_ACCOUNTING_IDENTITY_UNRESOLVED",
                        from_stage=Stage.MANDATE_AUTHORIZATION,
                        to_stage=event.stage,
                        severity="medium",
                        explanation=(
                            f"Authoritative {key!r} evidence in the same session "
                            "and operation exposes no common typed payment identity."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events, event],
                    ),
                )
            continue

        relevant_events.append(event)
        if event.session_id != session_id:
            classified = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_SESSION_MISMATCH",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RECONCILIATION,
                    severity="high",
                    explanation=(
                        f"Accounting evidence {key!r} for the authorized payment "
                        f"is attributed to session {event.session_id!r}, not "
                        f"{session_id!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, event],
                ),
            )
            continue
        if event.operation_id != operation_id:
            classified = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_OPERATION_MISMATCH",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.RECONCILIATION,
                    severity="high",
                    explanation=(
                        f"Accounting evidence {key!r} is attributed to operation "
                        f"{event.operation_id!r}, not {operation_id!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, event],
                ),
            )
            continue

        record = _amount(event)
        if record is None:
            classified = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_AMOUNT_INVALID",
                    from_stage=event.stage,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        f"Accounting evidence {key!r} must carry a non-negative "
                        "integer amount_minor and a non-empty asset."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, event],
                ),
            )
            continue
        if record.asset != asset:
            classified = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_ASSET_MISMATCH",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=event.stage,
                    severity="high",
                    explanation=(
                        f"Accounting evidence {key!r} uses asset {record.asset!r}; "
                        f"the authorization uses {asset!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, event],
                ),
            )
            continue
        valid.append(record)

    if classified:
        return _Selection(record=None, classified_failure=True)

    if valid:
        amounts = {record.amount for record in valid}
        if len(amounts) > 1:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_EVIDENCE_CONFLICT",
                    from_stage=valid[0].event.stage,
                    to_stage=Stage.RECONCILIATION,
                    severity="high",
                    explanation=(
                        f"Authoritative {key!r} records disagree: "
                        f"{sorted(str(amount) for amount in amounts)!r}."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, *(record.event for record in valid)],
                ),
            )
            return _Selection(record=None, classified_failure=True)
        return _Selection(record=valid[-1])

    if missing_code is not None:
        _append_unique(
            findings,
            seen,
            _finding(
                code=missing_code,
                from_stage=Stage.MANDATE_AUTHORIZATION,
                to_stage=Stage.RECONCILIATION,
                severity="medium",
                explanation=missing_explanation or f"Required {key!r} evidence is missing.",
                operation_id=operation_id,
                evidence=[*contract.events, *relevant_events],
            ),
        )
    return _Selection(record=None)


def _select_external_status(
    events: Iterable[StateEvent],
    *,
    session_id: str,
    operation_id: str,
    identity: Mapping[str, str],
    contract: _Contract,
    findings: list[Finding],
    seen: set[tuple[str, str | None]],
) -> bool:
    candidates: list[StateEvent] = []
    classified = False
    for event in events:
        if (
            event.key != "session_remainder_reconciliation_status"
            or event.stage != Stage.RECONCILIATION
            or not event.authoritative
        ):
            continue
        relation = _identity_relation(identity, event)
        same_scope = (
            event.session_id == session_id and event.operation_id == operation_id
        )
        if relation == "matched" and same_scope:
            candidates.append(event)
        elif same_scope:
            classified = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_IDENTITY_UNRESOLVED",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        "External remainder reconciliation cannot be tied to the "
                        "authorized payment through a stable typed identity."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, event],
                ),
            )

    if classified:
        return False
    if candidates and _status(candidates[-1].value) in _COMPLETE_STATUSES:
        return True
    _append_unique(
        findings,
        seen,
        _finding(
            code="SESSION_REMAINDER_EVIDENCE_MISSING",
            from_stage=Stage.RECONCILIATION,
            to_stage=Stage.RECONCILIATION,
            severity="medium",
            explanation=(
                "The external_reconciliation remainder policy lacks an "
                "authoritative completed reconciliation status."
            ),
            operation_id=operation_id,
            evidence=[*contract.events, *candidates],
        ),
    )
    return False


def _claim_amounts(
    events: Iterable[StateEvent],
    *,
    key: str,
    session_id: str,
    operation_id: str,
    identity: Mapping[str, str],
    asset: str,
) -> list[_Amount]:
    records: list[_Amount] = []
    for event in events:
        if event.key != key:
            continue
        if event.stage not in {Stage.CLAIMED_RESULT, Stage.RECEIPT, Stage.RECONCILIATION}:
            continue
        if event.session_id != session_id or event.operation_id != operation_id:
            continue
        if _identity_relation(identity, event) != "matched":
            continue
        record = _amount(event)
        if record is not None and record.asset == asset:
            records.append(record)
    return records


def _ceiling_groups(
    events: Iterable[StateEvent],
    session_id: str,
) -> dict[str | None, list[StateEvent]]:
    groups: dict[str | None, list[StateEvent]] = {}
    for event in _authoritative_events(
        events,
        key="authorized_ceiling_minor",
        stages={Stage.MANDATE_AUTHORIZATION},
    ):
        if event.session_id == session_id:
            groups.setdefault(event.operation_id, []).append(event)
    return groups


def _select_ceiling(
    events: list[StateEvent],
    *,
    operation_id: str,
    contract: _Contract,
    findings: list[Finding],
    seen: set[tuple[str, str | None]],
) -> tuple[_Amount | None, dict[str, str] | None]:
    parsed: list[_Amount] = []
    fingerprints: set[tuple[tuple[str, str], ...]] = set()
    classified = False

    for event in events:
        record = _amount(event)
        fingerprint = _identity_fingerprint(event)
        if record is None:
            classified = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_AMOUNT_INVALID",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        "Authorized ceiling must carry a non-negative integer "
                        "amount_minor and a non-empty asset."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, event],
                ),
            )
            continue
        if fingerprint is None:
            classified = True
            _append_unique(
                findings,
                seen,
                _finding(
                    code="SESSION_ACCOUNTING_IDENTITY_UNRESOLVED",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        "Authorized-ceiling evidence exposes no typed payment identity."
                    ),
                    operation_id=operation_id,
                    evidence=[*contract.events, event],
                ),
            )
            continue
        parsed.append(record)
        fingerprints.add(fingerprint)

    if classified:
        return None, None
    if not parsed:
        return None, None

    amounts = {(record.amount, record.asset) for record in parsed}
    if len(amounts) > 1 or len(fingerprints) > 1:
        _append_unique(
            findings,
            seen,
            _finding(
                code="SESSION_ACCOUNTING_EVIDENCE_CONFLICT",
                from_stage=Stage.MANDATE_AUTHORIZATION,
                to_stage=Stage.RECONCILIATION,
                severity="high",
                explanation=(
                    "Authoritative ceiling records in one session and operation "
                    "disagree about amount, asset, or typed payment identity."
                ),
                operation_id=operation_id,
                evidence=[*contract.events, *(record.event for record in parsed)],
            ),
        )
        return None, None

    identity, valid = _identity_profile([record.event for record in parsed])
    if not valid:
        _append_unique(
            findings,
            seen,
            _finding(
                code="SESSION_ACCOUNTING_IDENTITY_UNRESOLVED",
                from_stage=Stage.MANDATE_AUTHORIZATION,
                to_stage=Stage.RECONCILIATION,
                severity="high",
                explanation="Authoritative ceiling identities conflict.",
                operation_id=operation_id,
                evidence=[*contract.events, *(record.event for record in parsed)],
            ),
        )
        return None, None
    return parsed[-1], identity


def verify_payment_session_accounting(
    events: Iterable[StateEvent],
) -> list[Finding]:
    """Verify wallet settlement and payment-session accounting separately.

    Amounts are exact non-negative integer minor units carried as
    ``{"amount_minor": "…", "asset": "…"}``. A clean result requires one
    session, operation, asset, and typed payment identity across the authorized
    ceiling, independent settlement, authoritative session debit, and any
    remaining-budget evidence.
    """

    materialized = list(events)
    findings: list[Finding] = []
    seen: set[tuple[str, str | None]] = set()

    raw_contracts = [
        event
        for event in materialized
        if event.key == "payment_session_accounting_contract"
        and event.stage in {Stage.POLICY_DECISION, Stage.MANDATE_AUTHORIZATION}
    ]
    contracts: dict[str | None, _Contract] = {}
    conflicted_scopes: set[str | None] = set()

    for event in raw_contracts:
        required, debit_basis, remainder_policy, valid = _contract(event.value)
        if not required:
            continue
        if not valid or debit_basis is None or remainder_policy is None:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="PAYMENT_SESSION_ACCOUNTING_CONTRACT_INVALID",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        "The accounting contract must declare a supported "
                        "debit_basis and remainder_policy."
                    ),
                    operation_id=event.operation_id,
                    evidence=[event],
                ),
            )
            continue

        scope = event.session_id
        if scope in conflicted_scopes:
            continue
        existing = contracts.get(scope)
        if existing is not None and (
            existing.debit_basis != debit_basis
            or existing.remainder_policy != remainder_policy
        ):
            _append_unique(
                findings,
                seen,
                _finding(
                    code="PAYMENT_SESSION_ACCOUNTING_CONTRACT_CONFLICT",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RECONCILIATION,
                    severity="high",
                    explanation=(
                        "The same accounting-contract scope declares conflicting "
                        "debit or remainder semantics."
                    ),
                    operation_id=event.operation_id,
                    evidence=[*existing.events, event],
                ),
            )
            contracts.pop(scope, None)
            conflicted_scopes.add(scope)
            continue

        if existing is None:
            contracts[scope] = _Contract(
                events=(event,),
                debit_basis=debit_basis,
                remainder_policy=remainder_policy,
            )
        else:
            contracts[scope] = _Contract(
                events=(*existing.events, event),
                debit_basis=debit_basis,
                remainder_policy=remainder_policy,
            )

    # Only session-ledger or application-accounting evidence creates a new
    # session-level contract obligation. A settlement/ceiling event in another
    # session that shares a payment identity is classified against the intended
    # session instead of also being called a new session with a missing contract.
    accounting_evidence_sessions = {
        event.session_id
        for event in materialized
        if event.key in _SESSION_ACCOUNTING_KEYS and event.session_id is not None
    }
    all_evidence_sessions = {
        event.session_id
        for event in materialized
        if event.key in _ACCOUNTING_TRIGGER_KEYS and event.session_id is not None
    }
    global_contract = contracts.get(None)
    for session_id in sorted(accounting_evidence_sessions):
        if session_id in conflicted_scopes:
            continue
        if session_id not in contracts and global_contract is None:
            evidence = [
                event
                for event in materialized
                if event.session_id == session_id
                and event.key in _SESSION_ACCOUNTING_KEYS
            ]
            _append_unique(
                findings,
                seen,
                _finding(
                    code="PAYMENT_SESSION_ACCOUNTING_CONTRACT_MISSING",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        f"Session {session_id!r} exposes accounting evidence but "
                        "no accounting semantics contract."
                    ),
                    operation_id=evidence[0].operation_id if evidence else None,
                    evidence=evidence,
                ),
            )

    if None in conflicted_scopes:
        return findings

    contract_sessions = {
        session_id for session_id in contracts if session_id is not None
    }
    if global_contract is not None:
        contract_sessions.update(all_evidence_sessions)

    for session_id in sorted(contract_sessions):
        if session_id in conflicted_scopes:
            continue
        session_contract = contracts.get(session_id)
        contract, compatible = _effective_contract(global_contract, session_contract)
        if not compatible:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="PAYMENT_SESSION_ACCOUNTING_CONTRACT_CONFLICT",
                    from_stage=Stage.POLICY_DECISION,
                    to_stage=Stage.RECONCILIATION,
                    severity="high",
                    explanation=(
                        f"Session {session_id!r} accounting semantics conflict "
                        "with the global accounting contract."
                    ),
                    operation_id=session_contract.event.operation_id
                    if session_contract is not None
                    else None,
                    evidence=[
                        *(global_contract.events if global_contract else ()),
                        *(session_contract.events if session_contract else ()),
                    ],
                ),
            )
            continue
        if contract is None:
            continue

        groups = _ceiling_groups(materialized, session_id)
        if not groups:
            _append_unique(
                findings,
                seen,
                _finding(
                    code="AUTHORIZED_CEILING_EVIDENCE_MISSING",
                    from_stage=Stage.MANDATE_AUTHORIZATION,
                    to_stage=Stage.RECONCILIATION,
                    severity="medium",
                    explanation=(
                        f"Session {session_id!r} has an accounting contract but "
                        "no authoritative authorized-ceiling evidence."
                    ),
                    operation_id=contract.event.operation_id,
                    evidence=[*contract.events],
                ),
            )
            continue

        for operation_id, ceiling_events in groups.items():
            if operation_id is None:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_ACCOUNTING_IDENTITY_UNRESOLVED",
                        from_stage=Stage.MANDATE_AUTHORIZATION,
                        to_stage=Stage.RECONCILIATION,
                        severity="medium",
                        explanation=(
                            "Authorized-ceiling evidence omits operation_id, so "
                            "ledger evidence cannot be scoped safely."
                        ),
                        operation_id=None,
                        evidence=[*contract.events, *ceiling_events],
                    ),
                )
                continue

            ceiling, identity = _select_ceiling(
                ceiling_events,
                operation_id=operation_id,
                contract=contract,
                findings=findings,
                seen=seen,
            )
            if ceiling is None or identity is None:
                continue

            settlement_selection = _select_amount(
                materialized,
                key="settled_amount_minor",
                stages={Stage.ACTUAL_SETTLEMENT_FINALITY},
                session_id=session_id,
                operation_id=operation_id,
                identity=identity,
                asset=ceiling.asset,
                findings=findings,
                seen=seen,
                contract=contract,
                missing_code="ACTUAL_SETTLEMENT_AMOUNT_EVIDENCE_MISSING",
                missing_explanation=(
                    "No authoritative settlement amount can be tied to the "
                    "authorized payment."
                ),
            )
            settlement = settlement_selection.record
            if settlement is None:
                continue

            combined_identity, valid_identity = _identity_profile(
                [ceiling.event, settlement.event]
            )
            if not valid_identity:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_ACCOUNTING_IDENTITY_UNRESOLVED",
                        from_stage=Stage.MANDATE_AUTHORIZATION,
                        to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        severity="high",
                        explanation=(
                            "Authorization and settlement evidence carry conflicting "
                            "typed payment identities."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events, ceiling.event, settlement.event],
                    ),
                )
                continue

            debit_selection = _select_amount(
                materialized,
                key="session_debit_minor",
                stages={Stage.POLICY_DECISION, Stage.PAYMENT_ATTEMPT, Stage.RECONCILIATION},
                session_id=session_id,
                operation_id=operation_id,
                identity=combined_identity,
                asset=ceiling.asset,
                findings=findings,
                seen=seen,
                contract=contract,
                missing_code="SESSION_DEBIT_EVIDENCE_MISSING",
                missing_explanation=(
                    "No authoritative session-debit amount can be tied to the "
                    "settled payment."
                ),
            )
            debit = debit_selection.record
            if debit is None:
                continue

            accounting_identity, valid_accounting_identity = _identity_profile(
                [ceiling.event, settlement.event, debit.event]
            )
            if not valid_accounting_identity:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_ACCOUNTING_IDENTITY_UNRESOLVED",
                        from_stage=Stage.MANDATE_AUTHORIZATION,
                        to_stage=Stage.RECONCILIATION,
                        severity="high",
                        explanation=(
                            "Ceiling, settlement, and session-debit evidence carry "
                            "conflicting typed payment identities."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            ceiling.event,
                            settlement.event,
                            debit.event,
                        ],
                    ),
                )
                continue

            if settlement.amount > ceiling.amount:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SETTLEMENT_EXCEEDS_AUTHORIZED_CEILING",
                        from_stage=Stage.MANDATE_AUTHORIZATION,
                        to_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        severity="critical",
                        explanation=(
                            f"Settlement {settlement.amount} exceeds authorized "
                            f"ceiling {ceiling.amount}."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events, ceiling.event, settlement.event],
                    ),
                )
            if debit.amount > ceiling.amount:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_DEBIT_EXCEEDS_AUTHORIZED_CEILING",
                        from_stage=Stage.MANDATE_AUTHORIZATION,
                        to_stage=Stage.RECONCILIATION,
                        severity="critical",
                        explanation=(
                            f"Session debit {debit.amount} exceeds authorized "
                            f"ceiling {ceiling.amount}."
                        ),
                        operation_id=operation_id,
                        evidence=[*contract.events, ceiling.event, debit.event],
                    ),
                )

            provider: _Amount | None = None
            if contract.debit_basis == "explicit_provider_amount":
                provider_selection = _select_amount(
                    materialized,
                    key="provider_debit_amount_minor",
                    stages={Stage.POLICY_DECISION, Stage.PAYMENT_ATTEMPT, Stage.RECONCILIATION},
                    session_id=session_id,
                    operation_id=operation_id,
                    identity=accounting_identity,
                    asset=ceiling.asset,
                    findings=findings,
                    seen=seen,
                    contract=contract,
                    missing_code="EXPLICIT_PROVIDER_AMOUNT_EVIDENCE_MISSING",
                    missing_explanation=(
                        "The explicit_provider_amount debit basis lacks an "
                        "authoritative provider amount."
                    ),
                )
                provider = provider_selection.record

            expected_debit = {
                "authorized_ceiling": ceiling.amount,
                "actual_settlement": settlement.amount,
                "explicit_provider_amount": provider.amount if provider else None,
            }[contract.debit_basis]
            if expected_debit is not None and debit.amount != expected_debit:
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_DEBIT_BASIS_MISMATCH",
                        from_stage=Stage.POLICY_DECISION,
                        to_stage=Stage.RECONCILIATION,
                        severity="high",
                        explanation=(
                            f"Contract debit_basis={contract.debit_basis!r} expects "
                            f"{expected_debit}; authoritative session debit is "
                            f"{debit.amount}."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            ceiling.event,
                            settlement.event,
                            debit.event,
                            provider.event if provider else None,
                        ],
                    ),
                )

            credit_missing_code = (
                None
                if contract.remainder_policy == "external_reconciliation"
                else "SESSION_REMAINDER_EVIDENCE_MISSING"
            )
            credit_selection = _select_amount(
                materialized,
                key="session_credit_minor",
                stages={Stage.RECONCILIATION},
                session_id=session_id,
                operation_id=operation_id,
                identity=accounting_identity,
                asset=ceiling.asset,
                findings=findings,
                seen=seen,
                contract=contract,
                missing_code=credit_missing_code,
                missing_explanation=(
                    f"Contract remainder_policy={contract.remainder_policy!r} "
                    "requires authoritative session-credit evidence."
                ),
            )
            credit = credit_selection.record

            expected_credit: Decimal | None
            if contract.remainder_policy == "not_credited":
                expected_credit = Decimal(0)
            elif contract.remainder_policy == "credited":
                expected_credit = max(debit.amount - settlement.amount, Decimal(0))
            else:
                expected_credit = None
                _select_external_status(
                    materialized,
                    session_id=session_id,
                    operation_id=operation_id,
                    identity=accounting_identity,
                    contract=contract,
                    findings=findings,
                    seen=seen,
                )

            if (
                expected_credit is not None
                and credit is not None
                and credit.amount != expected_credit
            ):
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_REMAINDER_POLICY_MISMATCH",
                        from_stage=Stage.ACTUAL_SETTLEMENT_FINALITY,
                        to_stage=Stage.RECONCILIATION,
                        severity="high",
                        explanation=(
                            f"Remainder policy expects session credit "
                            f"{expected_credit}; authoritative credit is "
                            f"{credit.amount}."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            settlement.event,
                            debit.event,
                            credit.event,
                        ],
                    ),
                )

            effective_credit = credit.amount if credit is not None else None
            net_session_spend = (
                debit.amount - effective_credit
                if effective_credit is not None
                else None
            )

            before_selection = _select_amount(
                materialized,
                key="session_remaining_before_minor",
                stages={Stage.POLICY_DECISION, Stage.PAYMENT_ATTEMPT, Stage.RECONCILIATION},
                session_id=session_id,
                operation_id=operation_id,
                identity=accounting_identity,
                asset=ceiling.asset,
                findings=findings,
                seen=seen,
                contract=contract,
            )
            after_selection = _select_amount(
                materialized,
                key="session_remaining_after_minor",
                stages={Stage.RECONCILIATION},
                session_id=session_id,
                operation_id=operation_id,
                identity=accounting_identity,
                asset=ceiling.asset,
                findings=findings,
                seen=seen,
                contract=contract,
            )
            before = before_selection.record
            after = after_selection.record

            if (
                (before is None) != (after is None)
                and not (
                    before_selection.classified_failure
                    or after_selection.classified_failure
                )
            ):
                _append_unique(
                    findings,
                    seen,
                    _finding(
                        code="SESSION_REMAINING_BALANCE_EVIDENCE_MISSING",
                        from_stage=Stage.POLICY_DECISION,
                        to_stage=Stage.RECONCILIATION,
                        severity="medium",
                        explanation=(
                            "Remaining-budget reconciliation requires both before "
                            "and after authoritative balances."
                        ),
                        operation_id=operation_id,
                        evidence=[
                            *contract.events,
                            before.event if before else None,
                            after.event if after else None,
                        ],
                    ),
                )
            elif (
                before is not None
                and after is not None
                and effective_credit is not None
            ):
                expected_after = before.amount - debit.amount + effective_credit
                if expected_after < 0 or after.amount != expected_after:
                    _append_unique(
                        findings,
                        seen,
                        _finding(
                            code="SESSION_REMAINING_BALANCE_MISMATCH",
                            from_stage=Stage.POLICY_DECISION,
                            to_stage=Stage.RECONCILIATION,
                            severity="high",
                            explanation=(
                                f"Session balance should reconcile to {expected_after}; "
                                f"authoritative after balance is {after.amount}."
                            ),
                            operation_id=operation_id,
                            evidence=[
                                *contract.events,
                                before.event,
                                debit.event,
                                credit.event if credit else None,
                                after.event,
                            ],
                        ),
                    )

            spend_claims = _claim_amounts(
                materialized,
                key="claimed_session_spend_minor",
                session_id=session_id,
                operation_id=operation_id,
                identity=accounting_identity,
                asset=ceiling.asset,
            )
            if net_session_spend is not None:
                for claim in spend_claims:
                    if claim.amount != net_session_spend:
                        _append_unique(
                            findings,
                            seen,
                            _finding(
                                code="CLAIMED_SESSION_SPEND_MISMATCH",
                                from_stage=Stage.CLAIMED_RESULT,
                                to_stage=Stage.RECONCILIATION,
                                severity="high",
                                explanation=(
                                    f"Claimed session spend {claim.amount} conflicts "
                                    f"with authoritative net session consumption "
                                    f"{net_session_spend}."
                                ),
                                operation_id=operation_id,
                                evidence=[
                                    *contract.events,
                                    debit.event,
                                    credit.event if credit else None,
                                    claim.event,
                                ],
                            ),
                        )

            remaining_claims = _claim_amounts(
                materialized,
                key="claimed_session_remaining_minor",
                session_id=session_id,
                operation_id=operation_id,
                identity=accounting_identity,
                asset=ceiling.asset,
            )
            if after is not None:
                for claim in remaining_claims:
                    if claim.amount != after.amount:
                        _append_unique(
                            findings,
                            seen,
                            _finding(
                                code="CLAIMED_SESSION_REMAINING_MISMATCH",
                                from_stage=Stage.CLAIMED_RESULT,
                                to_stage=Stage.RECONCILIATION,
                                severity="high",
                                explanation=(
                                    f"Claimed remaining budget {claim.amount} conflicts "
                                    f"with authoritative session balance {after.amount}."
                                ),
                                operation_id=operation_id,
                                evidence=[*contract.events, after.event, claim.event],
                            ),
                        )

    return findings


__all__ = ["verify_payment_session_accounting"]
