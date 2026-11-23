import json
from pathlib import Path

from opspilot.evaluation.metrics import aggregate_scores, score_case


def _evaluation_cases():
    path = Path(__file__).resolve().parents[1] / "data/eval/ground_truth/scenarios.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def test_evaluation_measures_grounding_approval_and_execution():
    case = _evaluation_cases()[0]
    evidence_ids = ["log:db-pool-error", "runbook:pool-guide"]
    result = {
        "scenario_id": case["scenario_id"],
        "report": {
            "incident_summary": "Checkout connection pool incident.",
            "investigation_steps": [],
            "evidence_refs": evidence_ids,
            "probable_root_cause": case["expected_root_cause"],
            "supporting_reasoning": (
                "The observed connection acquisition timeout aligns with the cited evidence."
            ),
            "uncertainty": "The synthetic record contains the relevant error events.",
            "resolution_recommendation": case["expected_resolution"],
            "should_propose_action": True,
            "action_reason": "Restore the synthetic asset indicator after review.",
        },
        "observations": [
            {
                "tool_name": "search_logs",
                "records": [{"event_code": code} for code in case["relevant_evidence_codes"]],
                "evidence_refs": [evidence_ids[0]],
            },
            {
                "tool_name": "search_runbooks",
                "records": [],
                "evidence_refs": [evidence_ids[1]],
            },
        ],
        "proposed_action": {
            "action_type": case["expected_action"]["type"],
            "parameters": {
                "expected_state": case["expected_action"]["from"],
                "new_state": case["expected_action"]["to"],
            },
        },
        "approval": {"decision": "approved"},
        "execution": {"status": "succeeded", "result": {"new_state": "healthy"}},
    }
    scores = score_case(case, result)
    assert scores["structured_output_valid"] is True
    assert scores["root_cause_token_recall"] == 1.0
    assert scores["evidence_code_recall"] == 1.0
    assert scores["citation_grounding_precision"] == 1.0
    assert scores["approval_compliant"] is True
    assert scores["execution_correct"] is True
    summary = aggregate_scores([scores])
    assert summary["case_count"] == 1
    assert summary["metrics"]["execution_correct"] == 1.0


def test_evaluation_flags_fabricated_citations_and_unsafe_ambiguous_action():
    case = next(
        item for item in _evaluation_cases() if item["scenario_id"] == "ambiguous-network-symptom"
    )
    result = {
        "report": {
            "incident_summary": "Occasional reporting latency spike.",
            "investigation_steps": [],
            "evidence_refs": ["log:invented"],
            "probable_root_cause": case["expected_root_cause"],
            "supporting_reasoning": (
                "No correlated error or dependency evidence appears in observations."
            ),
            "uncertainty": "The sample is insufficient.",
            "resolution_recommendation": case["expected_resolution"],
            "should_propose_action": True,
            "action_reason": "Restore synthetic asset.",
        },
        "observations": [
            {
                "tool_name": "search_logs",
                "records": [{"event_code": code} for code in case["relevant_evidence_codes"]],
                "evidence_refs": ["log:actual"],
            },
            {"tool_name": "search_runbooks", "records": [], "evidence_refs": []},
        ],
        "proposed_action": {"action_type": "set_asset_operational_state", "parameters": {}},
        "execution": None,
    }
    scores = score_case(case, result)
    assert scores["citation_grounding_precision"] == 0.0
    assert scores["action_proposal_correct"] is False
    assert scores["approval_compliant"] is False
