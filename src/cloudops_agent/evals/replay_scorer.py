"""Stage 2.5 Counterfactual Trajectory Scorer & Constitutional Cap Gate.

Implements the evaluation gate from Dream-RSI (`arXiv:2609.14858`) and
RRSI / Total Cost of Agency (`arXiv:2609.23790`):
1. Dead-End Collision Check: Verifies candidate playbooks never trigger recorded DISPROVEN_DEAD_END queries.
2. Counterfactual Value (`CF_Value`): Quantifies diagnostic step reduction over baseline.
3. Constitutional Cap (`<= 120` lines): Prevents playbook instruction bloat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from cloudops_agent.schemas.incident import DisprovenDeadEnd

MAX_CONSTITUTIONAL_LINES = 120
MIN_COUNTERFACTUAL_GAIN = 0.20


@dataclass(frozen=True)
class ReplayGateDecision:
    """Result of evaluating a candidate playbook through the Stage 2.5 Replay Gate."""

    promoted: bool
    cf_value: float
    line_count: int
    dead_end_collisions: List[str]
    rejection_reasons: List[str]


class CounterfactualReplayScorer:
    """Evaluates candidate playbooks against held-out incident trajectories."""

    def __init__(
        self,
        max_lines: int = MAX_CONSTITUTIONAL_LINES,
        min_cf_gain: float = MIN_COUNTERFACTUAL_GAIN,
    ) -> None:
        self.max_lines = max_lines
        self.min_cf_gain = min_cf_gain

    @staticmethod
    def compute_cf_value(baseline_steps: int, skill_steps: int) -> float:
        """Computes Counterfactual Value: (S_base - S_skill) / S_base."""
        if baseline_steps <= 0:
            raise ValueError("baseline_steps must be strictly positive")
        return (baseline_steps - skill_steps) / float(baseline_steps)

    def evaluate_candidate_playbook(
        self,
        playbook_markdown: str,
        baseline_steps: int,
        skill_steps: int,
        executed_tool_signatures: Sequence[str],
        known_dead_ends: Sequence[DisprovenDeadEnd],
    ) -> ReplayGateDecision:
        """Runs the 3-part Stage 2.5 promotion gate on a candidate playbook."""
        reasons: List[str] = []

        # 1. Dead-End Collision Check
        blocked_map = {de.avoid_tool_signature: de.hypothesis_id for de in known_dead_ends}
        collisions = [
            f"{sig} (from {blocked_map[sig]})"
            for sig in executed_tool_signatures
            if sig in blocked_map
        ]
        if collisions:
            reasons.append(f"Dead-end collision detected: {', '.join(collisions)}")

        # 2. Counterfactual Efficiency Gain
        cf_val = self.compute_cf_value(baseline_steps, skill_steps)
        if cf_val < self.min_cf_gain:
            reasons.append(
                f"Insufficient Counterfactual Value: CF_Value={cf_val:.2f} < {self.min_cf_gain:.2f}"
            )

        # 3. RRSI Constitutional Line Cap (<= 120 lines)
        lines = playbook_markdown.strip().splitlines()
        line_count = len(lines)
        if line_count > self.max_lines:
            reasons.append(
                f"Playbook exceeds RRSI constitutional line cap: {line_count} > {self.max_lines} lines"
            )

        return ReplayGateDecision(
            promoted=len(reasons) == 0,
            cf_value=cf_val,
            line_count=line_count,
            dead_end_collisions=collisions,
            rejection_reasons=reasons,
        )
