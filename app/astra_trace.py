from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .astra_spider import Finding, Stage, StateEvent, verify_causal_economic_outcome


@dataclass(frozen=True)
class Trace:
    trace_id: str
    protocol: str
    scenario: str
    events: tuple[StateEvent, ...]
    expected_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TraceReport:
    trace_id: str
    protocol: str
    scenario: str
    verdict: str
    evidence_hash: str
    findings: tuple[Finding, ...]


def state_event_from_mapping(raw: Mapping[str, Any]) -> StateEvent:
    return StateEvent(
        stage=Stage(raw["stage"]),
        key=str(raw["key"]),
        value=raw.get("value"),
        source=str(raw["source"]),
        authoritative=bool(raw.get("authoritative", False)),
        attempt_id=raw.get("attempt_id"),
        payment_id=raw.get("payment_id"),
        operation_id=raw.get("operation_id"),
        session_id=raw.get("session_id"),
        authorization_id=raw.get("authorization_id"),
        payload_hash=raw.get("payload_hash"),
        observed_at=raw.get("observed_at"),
    )


def trace_from_mapping(raw: Mapping[str, Any]) -> Trace:
    return Trace(
        trace_id=str(raw["trace_id"]),
        protocol=str(raw["protocol"]),
        scenario=str(raw["scenario"]),
        events=tuple(state_event_from_mapping(e) for e in raw["events"]),
        expected_codes=tuple(map(str, raw.get("expected_codes", []))),
    )


def load_trace(path: str | Path) -> Trace:
    with Path(path).open("r", encoding="utf-8") as handle:
        return trace_from_mapping(json.load(handle))


def canonical_trace_hash(trace: Trace) -> str:
    payload = {
        "trace_id": trace.trace_id,
        "protocol": trace.protocol,
        "scenario": trace.scenario,
        "events": [
            {**asdict(event), "stage": event.stage.value}
            for event in trace.events
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_trace_report(trace: Trace) -> TraceReport:
    findings = tuple(verify_causal_economic_outcome(trace.events))
    severities = {f.severity for f in findings}
    verdict = (
        "DIVERGED"
        if severities & {"critical", "high"}
        else "UNRESOLVED"
        if findings
        else "VERIFIED"
    )
    return TraceReport(
        trace_id=trace.trace_id,
        protocol=trace.protocol,
        scenario=trace.scenario,
        verdict=verdict,
        evidence_hash=canonical_trace_hash(trace),
        findings=findings,
    )


def report_to_mapping(report: TraceReport) -> dict[str, Any]:
    return {
        "trace_id": report.trace_id,
        "protocol": report.protocol,
        "scenario": report.scenario,
        "verdict": report.verdict,
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
