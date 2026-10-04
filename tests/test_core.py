"""Unit tests for Phase 1 Core Contracts, SQLite-JSON1 Tool Compactor, and Replay Scorer."""

from datetime import datetime, timezone

from cloudops_agent.evals.replay_scorer import CounterfactualReplayScorer
from cloudops_agent.middleware.tool_compactor import ZeroBloatToolCompactor
from cloudops_agent.schemas.incident import (
    DiagnosticHypothesis,
    IncidentPayload,
    SeverityLevel,
)


def test_incident_anti_anchoring_and_dead_end_recording() -> None:
    payload = IncidentPayload(
        incident_id="INC-2026-0930",
        gcp_project_id="prod-commerce-search",
        resource_uri="projects/prod-commerce-search/locations/global/catalogs/default_catalog",
        severity=SeverityLevel.P1,
        symptom_summary="Serving latency spiked to 4.2s with DEADLINE_EXCEEDED on conversational turns.",
        error_signatures=["DEADLINE_EXCEEDED", "DECODE_PREEMPTED"],
        anomaly_window_start=datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc),
        anomaly_window_end=datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc),
        active_hypotheses=[
            DiagnosticHypothesis(
                hypothesis_id="HYP-01",
                description="VPC Service Controls perimeter blocking egress to model endpoint",
                prior_confidence=0.65,
                falsification_probe="Check VPC-SC dry-run audit logs for policy violation entries",
                required_tools=["query_vpc_sc_audit_logs"],
            ),
            DiagnosticHypothesis(
                hypothesis_id="HYP-02",
                description="Downstream TPU decode preemption paired with client callback 10x retry amplification",
                prior_confidence=0.80,
                falsification_probe="Inspect runtime stream status for DECODE_PREEMPTED and retry counter reset",
                required_tools=["query_runtime_stream_logs"],
            ),
        ],
    )

    # Falsify HYP-01 and record it as a DisprovenDeadEnd
    dead_end = payload.record_dead_end(
        hypothesis_id="HYP-01",
        telemetry_proof_ref="Zero VPC-SC audit denials in cloudaudit.googleapis.com/policy",
        avoid_tool_signature="query_vpc_sc_audit_logs(project='prod-commerce-search')",
    )

    assert dead_end.hypothesis_id == "HYP-01"
    assert payload.active_hypotheses[0].is_falsified is True
    assert len(payload.disproven_dead_ends) == 1

    prompt_block = payload.to_anti_anchoring_prompt_block()
    assert "[DISPROVEN_DEAD_ENDS — STRICT DO-NOT-REPEAT BLOCK]" in prompt_block
    assert "BLOCKED_QUERY: `query_vpc_sc_audit_logs(project='prod-commerce-search')`" in prompt_block
    assert "[HYP-02]" in prompt_block


def test_zero_bloat_sqlite_json1_tool_compactor() -> None:
    compactor = ZeroBloatToolCompactor()
    noisy_logs = [
        {"id": f"log-{i}", "severity": "INFO", "component": "ingress", "latency_ms": 12}
        for i in range(95)
    ] + [
        {
            "id": f"err-{i}",
            "severity": "ERROR",
            "component": "serving_stream",
            "reason": "DECODE_PREEMPTED",
            "latency_ms": 4200,
        }
        for i in range(5)
    ]

    view = compactor.ingest_telemetry_payload("art-001", noisy_logs)
    assert view.total_records == 100
    assert view.severity_distribution == {"INFO": 95, "ERROR": 5}
    assert "COMPACTED_TOOL_ARTIFACT: `art-001`" in view.to_context_skeleton()

    error_rows = compactor.query_cached_tool_output(
        artifact_id="art-001",
        json_field="severity",
        equals_value="ERROR",
    )
    assert len(error_rows) == 5
    assert all(r["reason"] == "DECODE_PREEMPTED" for r in error_rows)


def test_counterfactual_replay_scorer_gates() -> None:
    scorer = CounterfactualReplayScorer(max_lines=120, min_cf_gain=0.20)
    concise_playbook = "\n".join([f"Step {i}: Check stream preemption metrics" for i in range(40)])

    # 1. Valid promotion: 10 baseline steps -> 5 skill steps (CF_Value = 0.50), 0 collisions, 40 lines
    decision_ok = scorer.evaluate_candidate_playbook(
        playbook_markdown=concise_playbook,
        baseline_steps=10,
        skill_steps=5,
        executed_tool_signatures=["query_runtime_stream_logs()"],
        known_dead_ends=[],
    )
    assert decision_ok.promoted is True
    assert decision_ok.cf_value == 0.50

    # 2. Rejected due to > 120 line RRSI Constitutional Cap violation
    bloated_playbook = "\n".join([f"Rule {i}" for i in range(135)])
    decision_bloated = scorer.evaluate_candidate_playbook(
        playbook_markdown=bloated_playbook,
        baseline_steps=10,
        skill_steps=5,
        executed_tool_signatures=["query_runtime_stream_logs()"],
        known_dead_ends=[],
    )
    assert decision_bloated.promoted is False
    assert any("constitutional line cap" in r for r in decision_bloated.rejection_reasons)


if __name__ == "__main__":
    test_incident_anti_anchoring_and_dead_end_recording()
    test_zero_bloat_sqlite_json1_tool_compactor()
    test_counterfactual_replay_scorer_gates()
    print("✅ All 3 Phase-1 core architecture test suites passed!")

