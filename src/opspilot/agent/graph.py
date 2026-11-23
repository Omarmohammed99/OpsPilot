"""Adaptive bounded LangGraph investigation, report, and approval workflow."""

from __future__ import annotations

import json
import math
import operator
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.agent.providers import InvestigatorProvider
from opspilot.agent.schemas import InvestigationReport
from opspilot.agent.tools import ReadOnlyInvestigationTools, validate_choice
from opspilot.config import get_settings
from opspilot.db.models import (
    Approval,
    AuditEvent,
    Incident,
    InvestigationFinding,
    InvestigationRun,
    ProposedAction,
)
from opspilot.services.actions import (
    ActionConflict,
    create_action_proposal,
    execute_approved_action,
)


class InvestigationState(TypedDict, total=False):
    run_id: str
    thread_id: str
    demo_session_id: str
    incident_id: str
    request: str
    conversation: Annotated[list[dict[str, str]], operator.add]
    incident: dict[str, Any]
    observations: Annotated[list[dict[str, Any]], operator.add]
    tool_trace: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, str]], operator.add]
    seen_calls: Annotated[list[str], operator.add]
    loop_turns: int
    tool_calls: int
    total_tokens: int
    estimated_cost_usd: float
    deadline_at: float
    next_node: str
    next_tool: dict[str, Any]
    report: dict[str, Any]
    proposed_action: dict[str, Any]
    approval_decision: str
    execution_result: dict[str, Any]
    status: str
    error_code: str | None
    budget_limited: bool


def thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}


def _compact_observations(
    observations: list[dict[str, Any]], max_chars: int = 12000
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    used = 0
    for observation in observations:
        item = {
            "observation_id": observation.get("observation_id"),
            "tool_name": observation.get("tool_name"),
            "summary": observation.get("summary"),
            "evidence_refs": observation.get("evidence_refs", []),
            "records": observation.get("records", [])[:8],
            "error": observation.get("error"),
        }
        rendered = json.dumps(item, ensure_ascii=False)
        if used + len(rendered) > max_chars:
            item["records"] = item["records"][:2]
            rendered = json.dumps(item, ensure_ascii=False)
        if used + len(rendered) > max_chars:
            continue
        compact.append(item)
        used += len(rendered)
    return compact


def build_investigation_graph(
    *,
    session_factory: sessionmaker[Session],
    knowledge_factory: type,
    provider: InvestigatorProvider,
    checkpointer: Any | None = None,
):
    settings = get_settings()
    tools = ReadOnlyInvestigationTools(session_factory, knowledge_factory)

    def load_incident(state: InvestigationState) -> dict[str, Any]:
        try:
            session_id = uuid.UUID(state["demo_session_id"])
            incident_id = uuid.UUID(state["incident_id"])
            with session_factory() as db:
                incident = db.scalar(
                    select(Incident).where(
                        Incident.id == incident_id, Incident.demo_session_id == session_id
                    )
                )
            if incident is None:
                raise ValueError("Incident does not belong to this demo session")
            return {
                "incident": {
                    "id": str(incident.id),
                    "external_key": incident.external_key,
                    "title": incident.title,
                    "description": incident.description[:1200],
                    "severity": incident.severity,
                    "status": incident.status,
                    "service_key": incident.service_key,
                    "opened_at": incident.opened_at.isoformat(),
                }
            }
        except Exception as exc:
            return {
                "status": "failed",
                "error_code": "incident_unavailable",
                "errors": [{"code": "incident_unavailable", "message": str(exc)[:240]}],
            }

    def choose_next_tool(state: InvestigationState) -> dict[str, Any]:
        if state.get("status") == "failed":
            return {"next_node": "finalize"}
        turns = state.get("loop_turns", 0)
        calls = state.get("tool_calls", 0)
        total_tokens = state.get("total_tokens", 0)
        now = time.time()
        deadline = state.get("deadline_at", now)
        if (
            turns >= settings.max_investigation_turns
            or calls >= settings.max_tool_calls_per_investigation
            or total_tokens >= settings.max_tokens_per_investigation
            or now >= deadline
        ):
            return {
                "next_node": "report",
                "budget_limited": True,
                "errors": [
                    {
                        "code": "investigation_budget_reached",
                        "message": "Investigation stopped at its server-side limit.",
                    }
                ],
            }
        context = {
            "request": state.get("request", ""),
            "incident": state.get("incident", {}),
            "observations": _compact_observations(state.get("observations", [])),
            "available_tools": [
                "get_incident",
                "list_related_assets",
                "search_logs",
                "list_work_orders",
                "search_incident_history",
                "search_runbooks",
                "finish",
            ],
            "budget": {
                "turns_remaining": settings.max_investigation_turns - turns,
                "read_tools_remaining": settings.max_tool_calls_per_investigation - calls,
                "tokens_remaining": settings.max_tokens_per_investigation - total_tokens,
                "seconds_remaining": max(0, int(deadline - now)),
            },
            "remaining_seconds": max(2, int(deadline - now)),
        }
        prompt_tokens_estimate = math.ceil((len(json.dumps(context, ensure_ascii=False)) + 700) / 3)
        remaining_cost_tokens = math.floor(
            (settings.max_estimated_cost_usd - state.get("estimated_cost_usd", 0.0))
            * 1_000_000
            / 5.0
        )
        completion_limit = min(
            600,
            settings.max_tokens_per_investigation - total_tokens - prompt_tokens_estimate,
            remaining_cost_tokens - prompt_tokens_estimate,
        )
        if completion_limit < 64:
            return {
                "next_node": "report",
                "budget_limited": True,
                "errors": [
                    {
                        "code": "estimated_cost_ceiling",
                        "message": (
                            "Further tool-selection calls would exceed the estimated run budget."
                        ),
                    }
                ],
            }
        context["completion_token_limit"] = completion_limit
        try:
            choice, used_tokens = provider.choose_tool(context)
            choice = validate_choice(choice)
        except Exception as exc:
            return {
                "status": "failed",
                "error_code": "planner_unavailable",
                "errors": [
                    {
                        "code": "planner_unavailable",
                        "message": (
                            "The configured model could not choose the next investigation step."
                        ),
                    }
                ],
                "tool_trace": [
                    {"kind": "model_error", "error_type": type(exc).__name__, "node": "planner"}
                ],
                "next_node": "finalize",
            }
        next_turns = turns + 1
        next_tokens = total_tokens + used_tokens
        estimated_cost = next_tokens * 5.0 / 1_000_000
        if estimated_cost >= settings.max_estimated_cost_usd:
            return {
                "loop_turns": next_turns,
                "total_tokens": next_tokens,
                "estimated_cost_usd": estimated_cost,
                "next_node": "report",
                "budget_limited": True,
                "errors": [
                    {
                        "code": "estimated_cost_ceiling",
                        "message": "The estimated per-run model cost ceiling was reached.",
                    }
                ],
            }
        trace = {
            "kind": "planner_decision",
            "turn": next_turns,
            "tool_name": choice.name,
            "rationale": choice.rationale[:400],
        }
        if choice.name == "finish":
            return {
                "loop_turns": next_turns,
                "total_tokens": next_tokens,
                "estimated_cost_usd": estimated_cost,
                "next_node": "report",
                "tool_trace": [trace],
            }
        args_text = json.dumps(choice.arguments, sort_keys=True, ensure_ascii=False)
        signature = f"{choice.name}:{args_text}"
        if signature in state.get("seen_calls", []):
            duplicate = {
                "observation_id": str(uuid.uuid4()),
                "tool_name": choice.name,
                "summary": "Repeated identical tool input was blocked by the workflow.",
                "records": [],
                "evidence_refs": [],
                "error": "duplicate_tool_call",
            }
            return {
                "loop_turns": next_turns,
                "total_tokens": next_tokens,
                "estimated_cost_usd": estimated_cost,
                "observations": [duplicate],
                "tool_trace": [trace],
                "seen_calls": [signature],
                "next_node": "choose",
            }
        return {
            "loop_turns": next_turns,
            "total_tokens": next_tokens,
            "estimated_cost_usd": estimated_cost,
            "next_tool": {"name": choice.name, "arguments": choice.arguments},
            "seen_calls": [signature],
            "tool_trace": [trace],
            "next_node": "execute_tool",
        }

    def execute_read_tool(state: InvestigationState) -> dict[str, Any]:
        selected = state.get("next_tool", {})
        observation = tools.execute(
            selected.get("name", ""),
            selected.get("arguments", {}),
            demo_session_id=uuid.UUID(state["demo_session_id"]),
            incident_id=uuid.UUID(state["incident_id"]),
        )
        return {
            "observations": [observation.model_dump(mode="json")],
            "tool_calls": state.get("tool_calls", 0) + 1,
            "tool_trace": [
                {
                    "kind": "read_tool",
                    "tool_name": observation.tool_name,
                    "observation_id": observation.observation_id,
                    "status": "error" if observation.error else "ok",
                    "evidence_count": len(observation.evidence_refs),
                }
            ],
            "next_node": "choose",
        }

    def generate_report(state: InvestigationState) -> dict[str, Any]:
        observations = state.get("observations", [])
        evidence_ids = {
            ref for observation in observations for ref in observation.get("evidence_refs", [])
        }
        report_context = {
            "request": state.get("request", ""),
            "incident": state.get("incident", {}),
            "observations": _compact_observations(observations, max_chars=20000),
            "evidence_ids": sorted(evidence_ids),
            "budget_limited": state.get("budget_limited", False),
            "remaining_seconds": max(2, int(state.get("deadline_at", 0) - time.time())),
        }

        def budget_abstention() -> dict[str, Any]:
            available_refs = sorted(evidence_ids)[:40]
            steps = [
                {
                    "tool_name": item.get("tool_name", "unknown"),
                    "observation_id": item.get("observation_id", ""),
                    "summary": str(item.get("summary", ""))[:500],
                }
                for item in observations
            ][:12]
            report = InvestigationReport(
                incident_summary=str(state.get("incident", {}).get("title", "Incident")),
                investigation_steps=steps,
                evidence_refs=available_refs,
                probable_root_cause=(
                    "Uncertain: the investigation reached a server-side budget limit."
                ),
                supporting_reasoning=(
                    "This report uses only collected observations; no more model call was made."
                ),
                uncertainty=(
                    "A tool, token, time, or estimated-cost limit stopped further analysis."
                ),
                resolution_recommendation=(
                    "Review cited observations and gather evidence before changing system state."
                ),
                should_propose_action=False,
                action_reason="",
            )
            return {
                "report": report.model_dump(mode="json"),
                "status": "running",
                "next_node": "persist_report",
                "tool_trace": [{"kind": "report_abstained", "reason": "budget_limit"}],
            }

        repair_note = ""
        total_tokens = state.get("total_tokens", 0)
        for _attempt in range(2):
            prompt_tokens_estimate = math.ceil(
                (len(json.dumps(report_context, ensure_ascii=False)) + len(repair_note) + 1000) / 3
            )
            remaining_cost_tokens = math.floor(
                (settings.max_estimated_cost_usd - state.get("estimated_cost_usd", 0.0))
                * 1_000_000
                / 5.0
            )
            completion_limit = min(
                2200,
                settings.max_tokens_per_investigation - total_tokens - prompt_tokens_estimate,
                remaining_cost_tokens - prompt_tokens_estimate,
            )
            if (
                total_tokens >= settings.max_tokens_per_investigation
                or time.time() >= state.get("deadline_at", 0)
                or completion_limit < 128
            ):
                return budget_abstention()
            report_context["completion_token_limit"] = completion_limit
            try:
                report, used = provider.create_report(report_context, repair_note=repair_note)
                total_tokens += used
                estimated_cost = total_tokens * 5.0 / 1_000_000
                if estimated_cost > settings.max_estimated_cost_usd:
                    return budget_abstention()
                invalid_refs = set(report.evidence_refs) - evidence_ids
                invalid_steps = {
                    step.observation_id
                    for step in report.investigation_steps
                    if step.observation_id
                    not in {observation.get("observation_id") for observation in observations}
                }
                if invalid_refs or invalid_steps:
                    repair_note = (
                        "Remove unsupported evidence and step references. Invalid IDs: "
                        + ", ".join(sorted(invalid_refs))
                        + "; invalid observation IDs: "
                        + ", ".join(sorted(invalid_steps))
                    )[:1000]
                    continue
                return {
                    "report": report.model_dump(mode="json"),
                    "total_tokens": total_tokens,
                    "estimated_cost_usd": estimated_cost,
                    "next_node": "persist_report",
                    "tool_trace": [
                        {"kind": "report_generated", "evidence_count": len(report.evidence_refs)}
                    ],
                }
            except Exception as exc:
                return {
                    "status": "failed",
                    "error_code": "report_generation_failed",
                    "errors": [
                        {
                            "code": "report_generation_failed",
                            "message": "A valid structured report could not be generated.",
                        }
                    ],
                    "tool_trace": [
                        {"kind": "model_error", "error_type": type(exc).__name__, "node": "report"}
                    ],
                    "next_node": "finalize",
                }
        return {
            "status": "failed",
            "error_code": "report_evidence_validation_failed",
            "errors": [
                {
                    "code": "report_evidence_validation_failed",
                    "message": "The report contained evidence references that were not observed.",
                }
            ],
            "next_node": "finalize",
        }

    def persist_report(state: InvestigationState) -> dict[str, Any]:
        report = InvestigationReport.model_validate(state["report"])
        run_id = uuid.UUID(state["run_id"])
        session_id = uuid.UUID(state["demo_session_id"])
        with session_factory() as db, db.begin():
            run = db.scalar(
                select(InvestigationRun).where(
                    InvestigationRun.id == run_id,
                    InvestigationRun.demo_session_id == session_id,
                )
            )
            if run is None:
                raise ValueError("Investigation run is outside the demo session")
            categories = [
                ("summary", report.incident_summary, report.supporting_reasoning),
                ("probable_root_cause", report.probable_root_cause, report.supporting_reasoning),
                ("resolution", report.resolution_recommendation, report.uncertainty),
            ]
            existing_categories = set(
                db.scalars(
                    select(InvestigationFinding.category).where(
                        InvestigationFinding.run_id == run.id
                    )
                )
            )
            for category, claim, explanation in categories:
                if category not in existing_categories:
                    db.add(
                        InvestigationFinding(
                            run_id=run.id,
                            category=category,
                            claim=claim,
                            explanation=explanation,
                            evidence_refs=[{"ref": item} for item in report.evidence_refs],
                        )
                    )
            proposal_data: dict[str, Any] = {}
            if report.should_propose_action:
                asset_id = None
                for observation in state.get("observations", []):
                    if observation.get("tool_name") == "list_related_assets":
                        candidate = next(
                            (
                                row
                                for row in observation.get("records", [])
                                if row.get("operational_state") == "degraded"
                            ),
                            None,
                        )
                        if candidate:
                            asset_id = uuid.UUID(candidate["id"])
                            break
                has_log_evidence = any(ref.startswith("log:") for ref in report.evidence_refs)
                if asset_id and has_log_evidence and report.action_reason:
                    proposal = create_action_proposal(
                        db,
                        run=run,
                        asset_id=asset_id,
                        reason=report.action_reason,
                        ttl_minutes=settings.approval_ttl_minutes,
                    )
                    run.status = "waiting_approval"
                    proposal_data = {
                        "run_id": str(run.id),
                        "action_id": str(proposal.id),
                        "payload_hash": proposal.payload_hash,
                        "action_type": proposal.action_type,
                        "parameters": proposal.parameters,
                        "expires_at": proposal.expires_at.isoformat(),
                    }
                    db.add(
                        AuditEvent(
                            demo_session_id=session_id,
                            incident_id=run.incident_id,
                            run_id=run.id,
                            action_id=proposal.id,
                            actor_id="agent",
                            event_type="action_proposed",
                            details={"payload_hash": proposal.payload_hash},
                        )
                    )
            run.budget = {
                "loop_turns": state.get("loop_turns", 0),
                "tool_calls": state.get("tool_calls", 0),
                "total_tokens": state.get("total_tokens", 0),
                "estimated_cost_usd": state.get("estimated_cost_usd", 0.0),
                "budget_limited": state.get("budget_limited", False),
            }
            run.model_provider = provider.provider_name
            run.model_id = provider.model_name
            db.add(
                AuditEvent(
                    demo_session_id=session_id,
                    incident_id=run.incident_id,
                    run_id=run.id,
                    actor_id="agent",
                    event_type="report_persisted",
                    details={"evidence_count": len(report.evidence_refs)},
                )
            )
        if proposal_data:
            return {
                "proposed_action": proposal_data,
                "status": "waiting_approval",
                "next_node": "approval_gate",
            }
        return {"status": "running", "next_node": "finalize"}

    def approval_gate(state: InvestigationState) -> dict[str, Any]:
        proposal = state.get("proposed_action", {})
        resumed = interrupt(
            {
                "type": "human_approval_required",
                "run_id": state["run_id"],
                "action_id": proposal.get("action_id"),
                "payload_hash": proposal.get("payload_hash"),
                "parameters": proposal.get("parameters"),
            }
        )
        if not isinstance(resumed, dict):
            return {
                "status": "failed",
                "error_code": "invalid_approval_resume",
                "next_node": "finalize",
            }
        try:
            with session_factory() as db:
                action_id = uuid.UUID(str(resumed.get("action_id", "")))
                with db.begin():
                    action = db.scalar(
                        select(ProposedAction).where(
                            ProposedAction.id == action_id,
                            ProposedAction.demo_session_id == uuid.UUID(state["demo_session_id"]),
                            ProposedAction.run_id == uuid.UUID(state["run_id"]),
                        )
                    )
                    approval = db.scalar(select(Approval).where(Approval.action_id == action_id))
                    if (
                        action is None
                        or approval is None
                        or resumed.get("payload_hash") != action.payload_hash
                        or action.payload_hash != proposal.get("payload_hash")
                        or approval.payload_hash != action.payload_hash
                        or approval.actor_session_id != uuid.UUID(state["demo_session_id"])
                        or approval.decision != resumed.get("decision")
                    ):
                        raise ValueError("Resumed approval does not match the persisted decision")
                    if approval.decision == "rejected":
                        return {
                            "approval_decision": "rejected",
                            "status": "running",
                            "next_node": "finalize",
                        }
            return {"approval_decision": "approved", "next_node": "execute_action"}
        except Exception:
            return {
                "status": "failed",
                "error_code": "approval_revalidation_failed",
                "errors": [
                    {
                        "code": "approval_revalidation_failed",
                        "message": "Approval could not be revalidated; no action was executed.",
                    }
                ],
                "next_node": "finalize",
            }

    def execute_action_node(state: InvestigationState) -> dict[str, Any]:
        try:
            result = execute_approved_action(
                session_factory,
                action_id=uuid.UUID(state["proposed_action"]["action_id"]),
                demo_session_id=uuid.UUID(state["demo_session_id"]),
            )
            failed = result["status"] == "failed"
            return {
                "execution_result": result,
                "status": "failed" if failed else "running",
                "error_code": "approved_action_stale" if failed else None,
                "next_node": "finalize",
                "tool_trace": [{"kind": "approved_action_executed", "status": result["status"]}],
            }
        except ActionConflict:
            return {
                "status": "failed",
                "error_code": "approved_action_rejected_by_safety_check",
                "errors": [
                    {
                        "code": "approved_action_rejected_by_safety_check",
                        "message": (
                            "The approved action failed a final safety check; no write was applied."
                        ),
                    }
                ],
                "next_node": "finalize",
            }

    def finalize(state: InvestigationState) -> dict[str, Any]:
        run_id = uuid.UUID(state["run_id"])
        session_id = uuid.UUID(state["demo_session_id"])
        status = "failed" if state.get("status") == "failed" else "completed"
        with session_factory() as db, db.begin():
            run = db.scalar(
                select(InvestigationRun).where(
                    InvestigationRun.id == run_id,
                    InvestigationRun.demo_session_id == session_id,
                )
            )
            if run:
                run.status = status
                run.finished_at = datetime.now(UTC)
                run.error_code = state.get("error_code")
                db.add(
                    AuditEvent(
                        demo_session_id=session_id,
                        incident_id=run.incident_id,
                        run_id=run.id,
                        actor_id="agent",
                        event_type=f"investigation_{status}",
                        details={"error_code": state.get("error_code")},
                    )
                )
        return {"status": status}

    def route_after_choice(
        state: InvestigationState,
    ) -> Literal["execute_tool", "report", "finalize", "choose"]:
        return state.get("next_node", "finalize")  # type: ignore[return-value]

    def route_after_report(state: InvestigationState) -> Literal["approval_gate", "finalize"]:
        return "approval_gate" if state.get("next_node") == "approval_gate" else "finalize"

    def route_after_approval(state: InvestigationState) -> Literal["execute_action", "finalize"]:
        return "execute_action" if state.get("next_node") == "execute_action" else "finalize"

    graph = StateGraph(InvestigationState)
    graph.add_node("load_incident", load_incident)
    graph.add_node("choose_tool", choose_next_tool)
    graph.add_node("execute_tool", execute_read_tool)
    graph.add_node("generate_report", generate_report)
    graph.add_node("persist_report", persist_report)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("execute_action", execute_action_node)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "load_incident")
    graph.add_conditional_edges(
        "load_incident",
        lambda state: "finalize" if state.get("status") == "failed" else "choose_tool",
        {"choose_tool": "choose_tool", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "choose_tool",
        route_after_choice,
        {
            "execute_tool": "execute_tool",
            "report": "generate_report",
            "finalize": "finalize",
            "choose": "choose_tool",
        },
    )
    graph.add_edge("execute_tool", "choose_tool")
    graph.add_edge("generate_report", "persist_report")
    graph.add_conditional_edges(
        "persist_report",
        route_after_report,
        {"approval_gate": "approval_gate", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {"execute_action": "execute_action", "finalize": "finalize"},
    )
    graph.add_edge("execute_action", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def initial_investigation_state(
    *,
    run_id: uuid.UUID,
    thread_id: str,
    demo_session_id: uuid.UUID,
    incident_id: uuid.UUID,
    request: str,
) -> InvestigationState:
    settings = get_settings()
    return {
        "run_id": str(run_id),
        "thread_id": thread_id,
        "demo_session_id": str(demo_session_id),
        "incident_id": str(incident_id),
        "request": request,
        "conversation": [{"role": "user", "content": request[:2000]}],
        "observations": [],
        "tool_trace": [],
        "errors": [],
        "seen_calls": [],
        "loop_turns": 0,
        "tool_calls": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "deadline_at": time.time() + settings.max_active_investigation_seconds,
        "status": "running",
    }


def resume_approval(
    graph: Any, *, thread_id: str, action_id: uuid.UUID, decision: str, payload_hash: str
) -> dict[str, Any]:
    return graph.invoke(
        Command(
            resume={"action_id": str(action_id), "decision": decision, "payload_hash": payload_hash}
        ),
        thread_config(thread_id),
    )
