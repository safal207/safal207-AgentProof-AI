from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .astra_compensation import is_fully_compensated
from .astra_spider import Finding, Stage, StateEvent, verify_causal_economic_outcome


HASH_PROFILE = "astra-trace-json-v1"
VALID_VERDICTS = frozenset({"VERIFIED", "COMPENSATED", "UNRESOLVED", "DIVERGED"})


@dataclass(frozen=True)
class Trace:
    trace_id: str
    protocol: str
    scenario: str
    events: tuple[StateEvent, ...]
    expected_codes: tuple[str, ...] = ()
    expected_verdict: str | None = None


@dataclass(frozen=True)
class TraceReport:
    trace_id: str
    protocol: str
    scenario: str
    verdict: str
    hash_profile: str
    evidence_hash: str
    findings: tuple[Finding, ...]


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_text(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when present")
    return value


def state_event_from_mapping(raw: Mapping[str, Any]) -> StateEvent:
    authoritative = raw.get("authoritative", False)
    if not isinstance(authoritative, bool):
        raise ValueError("authoritative must be a JSON boolean")

    try:
        stage = Stage(_required_text(raw, "stage"))
    except ValueError as exc:
        raise ValueError(f"invalid stage: {raw.get('stage')!r}") from exc

    return StateEvent(
        stage=stage,
        key=_required_text(raw, "key"),
        value=raw.get("value"),
        source=_required_text(raw, "source"),
        authoritative=authoritative,
        attempt_id=_optional_text(raw, "attempt_id"),
        payment_id=_optional_text(raw, "payment_id"),
        operation_id=_optional_text(raw, "operation_id"),
        session_id=_optional_text(raw, "session_id"),
        authorization_id=_optional_text(raw, "authorization_id"),
        payload_hash=_optional_text(raw, "payload_hash"),
        observed_at=raw.get("observed_at"),
    )


def trace_from_mapping(raw: Mapping[str, Any]) -> Trace:
    raw_events = raw.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("events must be a JSON array")

    raw_expected = raw.get("expected_codes", [])
    if not isinstance(raw_expected, list) or not all(
        isinstance(code, str) and code for code in raw_expected
    ):
        raise ValueError("expected_codes must be an array of non-empty strings")

    expected_verdict = raw.get("expected_verdict")
    if expected_verdict is not None:
        if not isinstance(expected_verdict, str) or expected_verdict not in VALID_VERDICTS:
            allowed = ", ".join(sorted(VALID_VERDICTS))
            raise ValueError(f"expected_verdict must be one of: {allowed}")

    return Trace(
        trace_id=_required_text(raw, "trace_id"),
        protocol=_required_text(raw, "protocol"),
        scenario=_required_text(raw, "scenario"),
        events=tuple(state_event_from_mapping(event) for event in raw_events),
        expected_codes=tuple(raw_expected),
        expected_verdict=expected_verdict,
    )


def load_trace(path: str | Path) -> Trace:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, Mapping):
        raise ValueError("trace fixture must be a JSON object")
    return trace_from_mapping(raw)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime is not valid trace evidence")
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not valid trace evidence")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported trace evidence type: {type(value).__name__}")


def canonical_trace_hash(trace: Trace) -> str:
    payload = {
        "hash_profile": HASH_PROFILE,
        "trace_id": trace.trace_id,
        "protocol": trace.protocol,
        "scenario": trace.scenario,
        "events": [_json_ready(asdict(event)) for event in trace.events],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_trace_report(trace: Trace) -> TraceReport:
    findings = tuple(verify_causal_economic_outcome(trace.events))
    severities = {finding.severity for finding in findings}
    verdict = (
        "VERIFIED"
        if not findings
        else "COMPENSATED"
        if is_fully_compensated(trace.events, findings)
        else "DIVERGED"
        if severities & {"critical", "high"}
        else "UNRESOLVED"
    )
    return TraceReport(
        trace_id=trace.trace_id,
        protocol=trace.protocol,
        scenario=trace.scenario,
        verdict=verdict,
        hash_profile=HASH_PROFILE,
        evidence_hash=canonical_trace_hash(trace),
        findings=findings,
    )


def report_to_mapping(report: TraceReport) -> dict[str, Any]:
    return {
        "trace_id": report.trace_id,
        "protocol": report.protocol,
        "scenario": report.scenario,
        "verdict": report.verdict,
        "hash_profile": report.hash_profile,
        "evidence_hash": report.evidence_hash,
        "findings": [
            {
                **asdict(finding),
                "from_stage": finding.from_stage.value,
                "to_stage": finding.to_stage.value,
                "evidence_sources": list(finding.evidence_sources),
            }
            for finding in report.findings
        ],
    }
