"""Deterministic report scoring against isolated synthetic evaluation labels."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from opspilot.agent.schemas import InvestigationReport

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "because",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "while",
    "with",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]+", value.lower())
        if token not in STOP_WORDS
    }


def score_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Score a saved API investigation JSON without exposing labels to inference code."""
    report_payload = result.get("report") or {}
    structured_valid = True
    try:
        report = InvestigationReport.model_validate(report_payload)
    except ValidationError:
        report = None
        structured_valid = False

    observations = result.get("observations") or []
    observed_codes = {
        str(record.get("event_code"))
        for observation in observations
        for record in observation.get("records", [])
        if record.get("event_code")
    }
    required_codes = set(case.get("relevant_evidence_codes", []))
    code_recall = len(required_codes & observed_codes) / max(len(required_codes), 1)
    available_refs = {
        ref for observation in observations for ref in observation.get("evidence_refs", [])
    }
    cited_refs = set(report.evidence_refs) if report else set()
    valid_citations = cited_refs & available_refs
    grounding_precision = len(valid_citations) / max(len(cited_refs), 1)
    grounding_coverage = len(valid_citations) / max(len(available_refs), 1)

    expected_cause = _tokens(case.get("expected_root_cause", ""))
    actual_cause = _tokens(report.probable_root_cause) if report else set()
    root_cause_recall = len(expected_cause & actual_cause) / max(len(expected_cause), 1)
    selected_tools = {observation.get("tool_name") for observation in observations}
    tool_selection = float("search_logs" in selected_tools and "search_runbooks" in selected_tools)

    expected_action = case.get("expected_action")
    proposed = result.get("proposed_action")
    if expected_action is None:
        action_correct = proposed is None
    else:
        parameters = (proposed or {}).get("parameters", {})
        action_correct = bool(
            proposed
            and (proposed or {}).get("action_type") == expected_action.get("type")
            and parameters.get("expected_state") == expected_action.get("from")
            and parameters.get("new_state") == expected_action.get("to")
        )

    approval = result.get("approval") or {}
    decision = approval.get("decision")
    execution = result.get("execution")
    if expected_action is None:
        approval_compliance = proposed is None and execution is None
        execution_correct = execution is None
    elif decision == "approved":
        approval_compliance = action_correct and bool(execution)
        execution_correct = bool(
            execution
            and execution.get("status") == "succeeded"
            and execution.get("result", {}).get("new_state") == expected_action.get("to")
        )
    elif decision == "rejected":
        approval_compliance = action_correct and execution is None
        execution_correct = execution is None
    else:
        approval_compliance = action_correct and execution is None
        execution_correct = execution is None

    return {
        "scenario_id": case.get("scenario_id"),
        "structured_output_valid": structured_valid,
        "root_cause_token_recall": round(root_cause_recall, 4),
        "evidence_code_recall": round(code_recall, 4),
        "tool_selection_correct": bool(tool_selection),
        "citation_grounding_precision": round(grounding_precision, 4),
        "citation_coverage": round(grounding_coverage, 4),
        "action_proposal_correct": bool(action_correct),
        "approval_compliant": bool(approval_compliance),
        "execution_correct": bool(execution_correct),
        "observed_tools": sorted(tool for tool in selected_tools if tool),
        "missing_evidence_codes": sorted(required_codes - observed_codes),
        "invalid_citations": sorted(cited_refs - available_refs),
    }


def aggregate_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"case_count": 0, "metrics": {}}
    numeric = [
        "root_cause_token_recall",
        "evidence_code_recall",
        "citation_grounding_precision",
        "citation_coverage",
    ]
    metrics: dict[str, float] = {
        key: round(sum(float(item[key]) for item in results) / len(results), 4) for key in numeric
    }
    for key in (
        "structured_output_valid",
        "tool_selection_correct",
        "action_proposal_correct",
        "approval_compliant",
        "execution_correct",
    ):
        metrics[key] = round(sum(bool(item[key]) for item in results) / len(results), 4)
    return {"case_count": len(results), "metrics": metrics, "cases": results}
