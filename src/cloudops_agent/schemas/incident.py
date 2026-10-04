"""Strongly-typed Incident, Hypothesis, and Negative-Knowledge (DISPROVEN_DEAD_END) contracts.

Implements Falsification-First Anti-Anchoring serialization to prevent multi-step
confirmation bias during autonomous cloud root-cause analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class SeverityLevel(str, Enum):
    """Production incident severity classification."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


@dataclass
class DisprovenDeadEnd:
    """Negative-Knowledge contract recording a falsified diagnostic hypothesis.

    Inspired by Stage 2.5 Counterfactual Replay (Dream-RSI): explicitly persists
    falsified claims and the telemetry proof that refuted them so downstream
    agent turns never re-probe known dead ends.
    """

    hypothesis_id: str
    falsified_claim: str
    telemetry_proof_ref: str
    avoid_tool_signature: str
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.hypothesis_id or len(self.hypothesis_id.strip()) < 1:
            raise ValueError("hypothesis_id must be non-empty")
        if len(self.falsified_claim.strip()) < 5:
            raise ValueError("falsified_claim must be at least 5 characters")
        if len(self.telemetry_proof_ref.strip()) < 3:
            raise ValueError("telemetry_proof_ref must be at least 3 characters")
        if len(self.avoid_tool_signature.strip()) < 3:
            raise ValueError("avoid_tool_signature must be at least 3 characters")


@dataclass
class DiagnosticHypothesis:
    """A testable, falsifiable root-cause hypothesis in the StateGraph."""

    hypothesis_id: str
    description: str
    prior_confidence: float
    falsification_probe: str
    required_tools: List[str] = field(default_factory=list)
    is_falsified: bool = False
    falsification_evidence: Optional[str] = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.prior_confidence <= 1.0):
            raise ValueError("prior_confidence must be between 0.0 and 1.0")
        if len(self.falsification_probe.strip()) < 5:
            raise ValueError("falsification_probe must specify a testable condition (>= 5 chars)")


@dataclass
class IncidentPayload:
    """Normalized cloud production incident state entering the 5-Node StateGraph."""

    incident_id: str
    gcp_project_id: str
    resource_uri: str
    symptom_summary: str
    anomaly_window_start: datetime
    anomaly_window_end: datetime
    severity: SeverityLevel = SeverityLevel.P1
    error_signatures: List[str] = field(default_factory=list)
    active_hypotheses: List[DiagnosticHypothesis] = field(default_factory=list)
    disproven_dead_ends: List[DisprovenDeadEnd] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.anomaly_window_end < self.anomaly_window_start:
            raise ValueError("anomaly_window_end must be greater than or equal to anomaly_window_start")

    def record_dead_end(
        self,
        hypothesis_id: str,
        telemetry_proof_ref: str,
        avoid_tool_signature: str,
    ) -> DisprovenDeadEnd:
        """Marks an active hypothesis as falsified and appends a DisprovenDeadEnd."""
        target_claim = hypothesis_id
        for hyp in self.active_hypotheses:
            if hyp.hypothesis_id == hypothesis_id:
                hyp.is_falsified = True
                hyp.falsification_evidence = telemetry_proof_ref
                target_claim = hyp.description
                break

        dead_end = DisprovenDeadEnd(
            hypothesis_id=hypothesis_id,
            falsified_claim=target_claim,
            telemetry_proof_ref=telemetry_proof_ref,
            avoid_tool_signature=avoid_tool_signature,
        )
        self.disproven_dead_ends.append(dead_end)
        return dead_end

    def to_anti_anchoring_prompt_block(self) -> str:
        """Serializes active hypotheses and disproven dead ends using Falsification-First framing.

        Prevents downstream verifier agents from rubber-stamping unverified claims
        by framing active hypotheses as potential FAILED_ATTEMPT candidates.
        """
        lines = [
            f"### [INCIDENT_CONTEXT: {self.incident_id} | Severity: {self.severity.value}]",
            f"- Resource: `{self.resource_uri}` (Project: `{self.gcp_project_id}`)",
            f"- Symptom: {self.symptom_summary}",
            "",
            "### [DISPROVEN_DEAD_ENDS — STRICT DO-NOT-REPEAT BLOCK]",
        ]
        if not self.disproven_dead_ends:
            lines.append("- None recorded yet.")
        else:
            for de in self.disproven_dead_ends:
                lines.append(
                    f"- [DEAD_END {de.hypothesis_id}] Falsified Claim: '{de.falsified_claim}' "
                    f"| Refuted By: `{de.telemetry_proof_ref}` | BLOCKED_QUERY: `{de.avoid_tool_signature}`"
                )

        lines.append("")
        lines.append("### [UNVERIFIED_HYPOTHESES / POTENTIAL_FAILED_ATTEMPTS — FALSIFY FIRST]")
        pending = [h for h in self.active_hypotheses if not h.is_falsified]
        if not pending:
            lines.append("- No pending hypotheses.")
        else:
            for h in pending:
                lines.append(
                    f"- [{h.hypothesis_id}] (Prior={h.prior_confidence:.2f}): {h.description} "
                    f"| Falsification Probe: {h.falsification_probe}"
                )
        return "\n".join(lines)


@dataclass
class RCAReport:
    """Structured Root-Cause Analysis & Remediation deliverable."""

    incident_id: str
    verified_root_cause: str
    causal_chain: List[str]
    remediation_commands: List[str]
    confidence_score: float = 0.90
    disproven_dead_ends: List[DisprovenDeadEnd] = field(default_factory=list)

