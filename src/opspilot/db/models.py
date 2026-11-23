"""Relational schema for synthetic demo operations and audit history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opspilot.db.base import Base

JsonType = JSON().with_variant(JSONB, "postgresql")


def now_utc() -> datetime:
    return datetime.now(UTC)


class DemoSession(Base):
    __tablename__ = "demo_sessions"
    __table_args__ = (
        CheckConstraint("is_template OR token_hash IS NOT NULL", name="valid_session_token"),
        CheckConstraint("status IN ('active','expired','revoked')", name="session_status_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    is_template: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seed_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    incidents: Mapped[list[Incident]] = relationship(back_populates="demo_session")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint(
            "demo_session_id", "external_key", name="uq_incident_session_external_key"
        ),
        CheckConstraint("severity IN ('low','medium','high','critical')", name="severity_value"),
        CheckConstraint(
            "status IN ('open','investigating','resolved','closed')", name="status_value"
        ),
        Index("ix_incidents_session_status_severity", "demo_session_id", "status", "severity"),
        Index("ix_incidents_session_service_opened", "demo_session_id", "service_key", "opened_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    demo_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    external_key: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    service_key: Mapped[str] = mapped_column(String(100), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    demo_session: Mapped[DemoSession] = relationship(back_populates="incidents")
    incident_assets: Mapped[list[IncidentAsset]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    investigation_runs: Mapped[list[InvestigationRun]] = relationship(back_populates="incident")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("demo_session_id", "asset_key", name="uq_asset_session_key"),
        CheckConstraint(
            "operational_state IN ('healthy','degraded','maintenance','offline')",
            name="asset_state_value",
        ),
        Index(
            "ix_assets_session_service_environment", "demo_session_id", "service_key", "environment"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    demo_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    asset_key: Mapped[str] = mapped_column(String(100), nullable=False)
    service_key: Mapped[str] = mapped_column(String(100), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, default="demo")
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    hostname_label: Mapped[str] = mapped_column(String(120), nullable=False)
    operational_state: Mapped[str] = mapped_column(String(24), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    incident_assets: Mapped[list[IncidentAsset]] = relationship(back_populates="asset")
    logs: Mapped[list[ApplicationLog]] = relationship(back_populates="asset")
    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="asset")


class IncidentAsset(Base):
    __tablename__ = "incident_assets"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), primary_key=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), primary_key=True
    )
    relationship_role: Mapped[str] = mapped_column(String(40), nullable=False, default="affected")

    incident: Mapped[Incident] = relationship(back_populates="incident_assets")
    asset: Mapped[Asset] = relationship(back_populates="incident_assets")


class ApplicationLog(Base):
    __tablename__ = "application_logs"
    __table_args__ = (
        Index("ix_logs_session_service_occurred", "demo_session_id", "service_key", "occurred_at"),
        Index("ix_logs_asset_occurred", "asset_id", "occurred_at"),
        Index("ix_logs_event_code", "event_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    demo_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    service_key: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    level: Mapped[str] = mapped_column(String(12), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    event_code: Mapped[str | None] = mapped_column(String(80))
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    asset: Mapped[Asset | None] = relationship(back_populates="logs")


class WorkOrder(Base):
    __tablename__ = "work_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','in_progress','completed','cancelled')",
            name="work_order_status_value",
        ),
        Index("ix_work_orders_session_asset_status", "demo_session_id", "asset_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    demo_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT")
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    order_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    asset: Mapped[Asset] = relationship(back_populates="work_orders")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("source_key", "version", name="uq_knowledge_source_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_key: Mapped[str] = mapped_column(String(180), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    license: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InvestigationRun(Base):
    __tablename__ = "investigation_runs"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_investigation_thread_id"),
        CheckConstraint(
            "status IN ('queued','running','waiting_approval','completed','failed','interrupted')",
            name="run_status_value",
        ),
        Index("ix_runs_incident_started", "incident_id", "started_at"),
        Index("ix_runs_status_started", "status", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    demo_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    request_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    model_provider: Mapped[str | None] = mapped_column(String(60))
    model_id: Mapped[str | None] = mapped_column(String(140))
    config_version: Mapped[str | None] = mapped_column(String(40))
    budget: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    incident: Mapped[Incident] = relationship(back_populates="investigation_runs")
    findings: Mapped[list[InvestigationFinding]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    proposed_actions: Mapped[list[ProposedAction]] = relationship(back_populates="run")


class InvestigationFinding(Base):
    __tablename__ = "investigation_findings"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )

    run: Mapped[InvestigationRun] = relationship(back_populates="findings")


class ProposedAction(Base):
    __tablename__ = "proposed_actions"
    __table_args__ = (
        UniqueConstraint("id", "payload_hash", name="uq_action_id_payload_hash"),
        CheckConstraint("action_type = 'set_asset_operational_state'", name="action_allowlist"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired','executed','failed')",
            name="action_status_value",
        ),
        Index("ix_actions_status_expiry", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    demo_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[InvestigationRun] = relationship(back_populates="proposed_actions")
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="action", cascade="all, delete-orphan"
    )
    execution: Mapped[ActionExecution | None] = relationship(back_populates="action", uselist=False)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_approval_action"),
        CheckConstraint("decision IN ('approved','rejected')", name="approval_decision_value"),
        ForeignKeyConstraint(
            ["action_id", "payload_hash"],
            ["proposed_actions.id", "proposed_actions.payload_hash"],
            name="fk_approval_action_exact_payload",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )

    action: Mapped[ProposedAction] = relationship(back_populates="approvals")


class ActionExecution(Base):
    __tablename__ = "action_executions"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_execution_action"),
        UniqueConstraint("idempotency_key", name="uq_execution_idempotency_key"),
        CheckConstraint("status IN ('succeeded','failed')", name="execution_status_value"),
        ForeignKeyConstraint(
            ["action_id", "payload_hash"],
            ["proposed_actions.id", "proposed_actions.payload_hash"],
            name="fk_execution_action_exact_payload",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    action: Mapped[ProposedAction] = relationship(back_populates="execution")


class ResolutionRecord(Base):
    __tablename__ = "resolution_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("proposed_actions.id", ondelete="RESTRICT")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_audit_event_key"),
        Index("ix_audit_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(
        String(160), nullable=False, default=lambda: str(uuid.uuid4())
    )
    demo_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("demo_sessions.id", ondelete="RESTRICT")
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("investigation_runs.id", ondelete="RESTRICT")
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("proposed_actions.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[str | None] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(120))
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
