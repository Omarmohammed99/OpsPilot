"""Validated provider and report contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ToolName = Literal[
    "get_incident",
    "list_related_assets",
    "search_logs",
    "list_work_orders",
    "search_incident_history",
    "search_runbooks",
    "finish",
]


class ToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=3, max_length=400)


class ReportStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    observation_id: str
    summary: str = Field(max_length=500)


class InvestigationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_summary: str = Field(min_length=10, max_length=1200)
    investigation_steps: list[ReportStep] = Field(max_length=12)
    evidence_refs: list[str] = Field(max_length=40)
    probable_root_cause: str = Field(min_length=3, max_length=1200)
    supporting_reasoning: str = Field(min_length=10, max_length=2500)
    uncertainty: str = Field(min_length=3, max_length=1200)
    resolution_recommendation: str = Field(min_length=3, max_length=1600)
    should_propose_action: bool
    action_reason: str = Field(max_length=500)


class ToolObservation(BaseModel):
    observation_id: str
    tool_name: str
    summary: str
    records: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    error: str | None = None


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    expected_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(default="", max_length=1000)
