from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IncidentInput(StrictModel):
    external_key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    severity: str
    service_key: str = Field(min_length=1, max_length=100)
    opened_at: datetime

    @field_validator("severity")
    @classmethod
    def supported_severity(cls, value: str) -> str:
        value = value.lower()
        if value not in {"low", "medium", "high", "critical"}:
            raise ValueError("severity must be low, medium, high, or critical")
        return value


class AssetInput(StrictModel):
    asset_key: str = Field(min_length=1, max_length=100)
    service_key: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=32)
    asset_type: str = Field(min_length=1, max_length=64)
    hostname_label: str = Field(min_length=1, max_length=120)
    operational_state: str
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("operational_state")
    @classmethod
    def supported_state(cls, value: str) -> str:
        if value not in {"healthy", "degraded", "maintenance", "offline"}:
            raise ValueError("unsupported asset operational state")
        return value


class LogInput(StrictModel):
    occurred_at: datetime
    level: str = Field(min_length=1, max_length=12)
    source: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1)
    event_code: str | None = Field(default=None, max_length=80)
    attributes: dict[str, Any] = Field(default_factory=dict)


class WorkOrderInput(StrictModel):
    order_type: str = Field(min_length=1, max_length=48)
    status: str = "open"
    summary: str = Field(min_length=1)
    opened_at: datetime
    closed_at: datetime | None = None


class ScenarioInput(StrictModel):
    scenario_id: str = Field(min_length=1, max_length=100)
    incident: IncidentInput
    asset: AssetInput
    logs: list[LogInput] = Field(min_length=1)
    work_orders: list[WorkOrderInput] = Field(default_factory=list)


class ScenarioDataset(StrictModel):
    seed_version: str
    seed: int
    scenarios: list[ScenarioInput] = Field(min_length=1)


class KnowledgeDocumentInput(StrictModel):
    source_key: str = Field(min_length=1, max_length=180)
    version: str = Field(min_length=1, max_length=32)
    document_type: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    service_key: str = "*"
    error_codes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    body: str = Field(min_length=1)


class RunbookDataset(StrictModel):
    documents: list[KnowledgeDocumentInput] = Field(min_length=1)
