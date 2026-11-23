"""Typed read-only operational tools with demo-session and incident scoping."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.agent.schemas import ToolChoice, ToolObservation
from opspilot.db.models import (
    ApplicationLog,
    Asset,
    Incident,
    IncidentAsset,
    WorkOrder,
)
from opspilot.retrieval.chroma_store import KnowledgeIndex

SECRET_VALUE = re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+")


def _redact_text(value: str, limit: int = 1000) -> str:
    return SECRET_VALUE.sub(r"\1=[REDACTED]", value)[:limit]


def _redact_attributes(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if re.search(r"(?i)(password|token|secret|credential|private.?key|api.?key)", str(key)):
                clean[str(key)] = "[REDACTED]"
            else:
                clean[str(key)] = _redact_attributes(item)
        return clean
    if isinstance(value, list):
        return [_redact_attributes(item) for item in value[:30]]
    if isinstance(value, str):
        return _redact_text(value, 300)
    return value


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LogSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    levels: list[Literal["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]] = Field(
        default_factory=list, max_length=5
    )
    event_codes: list[str] = Field(default_factory=list, max_length=12)
    query: str = Field(default="", max_length=160)
    start_at: datetime | None = None
    end_at: datetime | None = None
    limit: int = Field(default=25, ge=1, le=50)

    @field_validator("event_codes")
    @classmethod
    def trim_codes(cls, values: list[str]) -> list[str]:
        return [value.strip()[:80] for value in values if value.strip()]

    @model_validator(mode="after")
    def valid_window(self) -> LogSearchArgs:
        if self.start_at and self.end_at:
            start = self.start_at.replace(tzinfo=self.start_at.tzinfo or UTC)
            end = self.end_at.replace(tzinfo=self.end_at.tzinfo or UTC)
            self._check_window(start, end)
        return self

    @staticmethod
    def _check_window(start: datetime, end: datetime) -> None:
        if end <= start or end - start > timedelta(days=30):
            raise ValueError("Log search window must be positive and at most 30 days")


class LimitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=50)


class RunbookSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=300)
    error_codes: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=4, ge=1, le=8)

    @field_validator("error_codes")
    @classmethod
    def validate_codes(cls, values: list[str]) -> list[str]:
        valid = []
        for value in values:
            code = value.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", code):
                raise ValueError("Invalid error code filter")
            valid.append(code)
        return valid


class ReadOnlyInvestigationTools:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        knowledge_factory: type[KnowledgeIndex] = KnowledgeIndex,
    ) -> None:
        self.session_factory = session_factory
        self.knowledge_factory = knowledge_factory

    def execute(
        self,
        name: str,
        raw_arguments: dict[str, Any],
        *,
        demo_session_id: uuid.UUID,
        incident_id: uuid.UUID,
    ) -> ToolObservation:
        observation_id = str(uuid.uuid4())
        try:
            with self.session_factory() as db:
                incident = db.scalar(
                    select(Incident).where(
                        Incident.id == incident_id,
                        Incident.demo_session_id == demo_session_id,
                    )
                )
                if incident is None:
                    raise ValueError("Incident is outside the active demo session")
                if name == "get_incident":
                    return self._incident(observation_id, incident)
                if name == "list_related_assets":
                    return self._assets(db, observation_id, incident)
                if name == "search_logs":
                    return self._logs(db, observation_id, incident, raw_arguments)
                if name == "list_work_orders":
                    return self._work_orders(db, observation_id, incident, raw_arguments)
                if name == "search_incident_history":
                    return self._history(db, observation_id, incident, raw_arguments)
            if name == "search_runbooks":
                return self._runbooks(observation_id, raw_arguments, incident.service_key)
            raise ValueError("Tool is not in the read-only allowlist")
        except (ValueError, ValidationError, TypeError) as exc:
            return ToolObservation(
                observation_id=observation_id,
                tool_name=name,
                summary=f"Tool input rejected: {str(exc)[:300]}",
                error="invalid_tool_input",
            )
        except Exception:
            return ToolObservation(
                observation_id=observation_id,
                tool_name=name,
                summary="Read-only tool failed; no operational write was attempted.",
                error="tool_unavailable",
            )

    @staticmethod
    def _incident(observation_id: str, incident: Incident) -> ToolObservation:
        record = {
            "id": str(incident.id),
            "external_key": incident.external_key,
            "title": _redact_text(incident.title, 240),
            "description": _redact_text(incident.description, 1200),
            "severity": incident.severity,
            "status": incident.status,
            "service_key": incident.service_key,
            "opened_at": incident.opened_at.isoformat(),
        }
        return ToolObservation(
            observation_id=observation_id,
            tool_name="get_incident",
            summary=f"Loaded incident {incident.external_key} ({incident.severity}).",
            records=[record],
            evidence_refs=[f"incident:{incident.id}"],
        )

    @staticmethod
    def _assets(db: Session, observation_id: str, incident: Incident) -> ToolObservation:
        rows = db.execute(
            select(Asset)
            .join(IncidentAsset)
            .where(
                IncidentAsset.incident_id == incident.id,
                Asset.demo_session_id == incident.demo_session_id,
            )
        ).scalars()
        records = [
            {
                "id": str(asset.id),
                "asset_key": asset.asset_key,
                "service_key": asset.service_key,
                "asset_type": asset.asset_type,
                "environment": asset.environment,
                "operational_state": asset.operational_state,
                "attributes": _redact_attributes(asset.attributes),
            }
            for asset in rows
        ]
        return ToolObservation(
            observation_id=observation_id,
            tool_name="list_related_assets",
            summary=f"Found {len(records)} asset(s) linked to this incident.",
            records=records,
            evidence_refs=[f"asset:{item['id']}" for item in records],
        )

    @staticmethod
    def _logs(
        db: Session,
        observation_id: str,
        incident: Incident,
        raw_arguments: dict[str, Any],
    ) -> ToolObservation:
        args = LogSearchArgs.model_validate(raw_arguments)
        asset_ids = list(
            db.scalars(
                select(IncidentAsset.asset_id).where(IncidentAsset.incident_id == incident.id)
            )
        )
        statement = select(ApplicationLog).where(
            ApplicationLog.demo_session_id == incident.demo_session_id,
            ApplicationLog.service_key == incident.service_key,
            or_(ApplicationLog.asset_id.in_(asset_ids), ApplicationLog.asset_id.is_(None)),
        )
        start = args.start_at or (incident.opened_at - timedelta(hours=4))
        end = args.end_at or (incident.opened_at + timedelta(hours=4))
        start_comparable = start.replace(tzinfo=start.tzinfo or UTC)
        end_comparable = end.replace(tzinfo=end.tzinfo or UTC)
        LogSearchArgs._check_window(start_comparable, end_comparable)
        statement = statement.where(ApplicationLog.occurred_at.between(start, end))
        if args.levels:
            statement = statement.where(ApplicationLog.level.in_(args.levels))
        if args.event_codes:
            statement = statement.where(ApplicationLog.event_code.in_(args.event_codes))
        if args.query.strip():
            statement = statement.where(ApplicationLog.message.ilike(f"%{args.query.strip()}%"))
        logs = list(
            db.scalars(statement.order_by(ApplicationLog.occurred_at.desc()).limit(args.limit))
        )
        records = [
            {
                "id": str(log.id),
                "occurred_at": log.occurred_at.isoformat(),
                "level": log.level,
                "source": log.source,
                "message": _redact_text(log.message, 1000),
                "event_code": log.event_code,
                "asset_id": str(log.asset_id) if log.asset_id else None,
                "attributes": _redact_attributes(log.attributes),
            }
            for log in logs
        ]
        return ToolObservation(
            observation_id=observation_id,
            tool_name="search_logs",
            summary=(
                f"Matched {len(records)} log record(s) within the incident's session and "
                "time window."
            ),
            records=records,
            evidence_refs=[f"log:{item['id']}" for item in records],
        )

    @staticmethod
    def _work_orders(
        db: Session,
        observation_id: str,
        incident: Incident,
        raw_arguments: dict[str, Any],
    ) -> ToolObservation:
        args = LimitArgs.model_validate(raw_arguments)
        orders = list(
            db.scalars(
                select(WorkOrder)
                .where(
                    WorkOrder.demo_session_id == incident.demo_session_id,
                    WorkOrder.incident_id == incident.id,
                )
                .order_by(WorkOrder.opened_at.desc())
                .limit(args.limit)
            )
        )
        records = [
            {
                "id": str(order.id),
                "order_type": order.order_type,
                "status": order.status,
                "summary": _redact_text(order.summary, 800),
                "opened_at": order.opened_at.isoformat(),
                "closed_at": order.closed_at.isoformat() if order.closed_at else None,
            }
            for order in orders
        ]
        return ToolObservation(
            observation_id=observation_id,
            tool_name="list_work_orders",
            summary=f"Found {len(records)} work order(s) for this incident.",
            records=records,
            evidence_refs=[f"work-order:{item['id']}" for item in records],
        )

    @staticmethod
    def _history(
        db: Session,
        observation_id: str,
        incident: Incident,
        raw_arguments: dict[str, Any],
    ) -> ToolObservation:
        args = LimitArgs.model_validate(raw_arguments)
        incidents = list(
            db.scalars(
                select(Incident)
                .where(
                    Incident.demo_session_id == incident.demo_session_id,
                    Incident.service_key == incident.service_key,
                    Incident.id != incident.id,
                )
                .order_by(Incident.opened_at.desc())
                .limit(args.limit)
            )
        )
        records = [
            {
                "id": str(item.id),
                "external_key": item.external_key,
                "title": item.title,
                "severity": item.severity,
                "status": item.status,
                "opened_at": item.opened_at.isoformat(),
            }
            for item in incidents
        ]
        return ToolObservation(
            observation_id=observation_id,
            tool_name="search_incident_history",
            summary=f"Found {len(records)} prior same-service incident(s) in this visitor session.",
            records=records,
            evidence_refs=[f"history:{item['id']}" for item in records],
        )

    def _runbooks(
        self, observation_id: str, raw_arguments: dict[str, Any], service_key: str
    ) -> ToolObservation:
        args = RunbookSearchArgs.model_validate(raw_arguments)
        matches = self.knowledge_factory().retrieve(
            args.query,
            service_key=service_key,
            error_codes=args.error_codes,
            limit=args.limit,
        )
        records = [
            {
                "chunk_id": match["chunk_id"],
                "source_key": match["metadata"].get("source_key"),
                "title": match["metadata"].get("title"),
                "doc_type": match["metadata"].get("doc_type"),
                "content": _redact_text(match["text"], 1200),
                "distance": float(match["distance"]),
                "license": match["metadata"].get("license"),
            }
            for match in matches
        ]
        return ToolObservation(
            observation_id=observation_id,
            tool_name="search_runbooks",
            summary=f"Retrieved {len(records)} knowledge chunk(s) from ChromaDB.",
            records=records,
            evidence_refs=[f"runbook:{item['chunk_id']}" for item in records],
        )


TOOL_NAMES = {
    "get_incident",
    "list_related_assets",
    "search_logs",
    "list_work_orders",
    "search_incident_history",
    "search_runbooks",
}


def validate_choice(choice: ToolChoice) -> ToolChoice:
    if choice.name not in TOOL_NAMES and choice.name != "finish":
        raise ValueError("LLM selected an unavailable tool")
    return choice
