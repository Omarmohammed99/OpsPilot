"""Idempotent, provenance-tracked synthetic PostgreSQL and Chroma ingestion."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.datasets.loaders import load_runbook_dataset, load_scenario_dataset
from opspilot.db.ingestion_models import IngestionBatch, IngestionFailure
from opspilot.db.models import (
    ApplicationLog,
    Asset,
    DemoSession,
    Incident,
    IncidentAsset,
    KnowledgeDocument,
    WorkOrder,
)
from opspilot.retrieval.chroma_store import KnowledgeIndex

logger = logging.getLogger(__name__)
TEMPLATE_SESSION_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
SYNTHETIC_LICENSE = "CC-BY-4.0"


class IngestionRecordError(RuntimeError):
    def __init__(self, entity_type: str, entity_key: str, cause: Exception) -> None:
        self.entity_type = entity_type
        self.entity_key = entity_key
        self.cause = cause
        super().__init__(f"{entity_type} {entity_key} failed: {cause}")


def stable_uuid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"opspilot:{kind}:{key}")


def _upsert(db: Session, model: type, record_id: uuid.UUID, **values: Any) -> Any:
    instance = db.get(model, record_id)
    if instance is None:
        instance = model(id=record_id, **values)
        db.add(instance)
    else:
        for name, value in values.items():
            setattr(instance, name, value)
    return instance


def ensure_template_session(db: Session, seed_version: str) -> DemoSession:
    template = db.get(DemoSession, TEMPLATE_SESSION_ID)
    if template is None:
        template = DemoSession(
            id=TEMPLATE_SESSION_ID,
            token_hash=None,
            is_template=True,
            seed_version=seed_version,
            status="active",
            expires_at=None,
        )
        db.add(template)
    else:
        template.seed_version = seed_version
        template.is_template = True
        template.status = "active"
    return template


def ingest_scenarios(db: Session, *, dataset_path: Path | None = None) -> dict[str, int]:
    dataset = load_scenario_dataset(dataset_path)
    ensure_template_session(db, dataset.seed_version)
    counts = {
        "scenarios": 0,
        "incidents": 0,
        "assets": 0,
        "incident_assets": 0,
        "logs": 0,
        "work_orders": 0,
    }
    for scenario in dataset.scenarios:
        try:
            with db.begin_nested():
                incident_id = stable_uuid("incident", scenario.incident.external_key)
                asset_id = stable_uuid("asset", scenario.asset.asset_key)
                _upsert(
                    db,
                    Incident,
                    incident_id,
                    demo_session_id=TEMPLATE_SESSION_ID,
                    external_key=scenario.incident.external_key,
                    title=scenario.incident.title,
                    description=scenario.incident.description,
                    severity=scenario.incident.severity,
                    status="open",
                    service_key=scenario.incident.service_key,
                    opened_at=scenario.incident.opened_at,
                    is_synthetic=True,
                )
                _upsert(
                    db,
                    Asset,
                    asset_id,
                    demo_session_id=TEMPLATE_SESSION_ID,
                    asset_key=scenario.asset.asset_key,
                    service_key=scenario.asset.service_key,
                    environment=scenario.asset.environment,
                    asset_type=scenario.asset.asset_type,
                    hostname_label=scenario.asset.hostname_label,
                    operational_state=scenario.asset.operational_state,
                    attributes=scenario.asset.attributes,
                    is_synthetic=True,
                )
                link = db.get(IncidentAsset, (incident_id, asset_id))
                if link is None:
                    db.add(
                        IncidentAsset(
                            incident_id=incident_id, asset_id=asset_id, relationship_role="affected"
                        )
                    )
                for index, log in enumerate(scenario.logs):
                    log_key = f"{scenario.scenario_id}:{index}:{log.event_code or log.message}"
                    _upsert(
                        db,
                        ApplicationLog,
                        stable_uuid("log", log_key),
                        demo_session_id=TEMPLATE_SESSION_ID,
                        asset_id=asset_id,
                        service_key=scenario.incident.service_key,
                        occurred_at=log.occurred_at,
                        level=log.level,
                        source=log.source,
                        message=log.message,
                        event_code=log.event_code,
                        attributes=log.attributes,
                        is_synthetic=True,
                    )
                for index, order in enumerate(scenario.work_orders):
                    order_key = f"{scenario.scenario_id}:{index}:{order.order_type}"
                    _upsert(
                        db,
                        WorkOrder,
                        stable_uuid("work-order", order_key),
                        demo_session_id=TEMPLATE_SESSION_ID,
                        incident_id=incident_id,
                        asset_id=asset_id,
                        order_type=order.order_type,
                        status=order.status,
                        summary=order.summary,
                        opened_at=order.opened_at,
                        closed_at=order.closed_at,
                        is_synthetic=True,
                    )
            counts["scenarios"] += 1
            counts["incidents"] += 1
            counts["assets"] += 1
            counts["incident_assets"] += 1
            counts["logs"] += len(scenario.logs)
            counts["work_orders"] += len(scenario.work_orders)
        except Exception as exc:
            logger.exception(
                "Synthetic scenario ingestion failed", extra={"scenario_id": scenario.scenario_id}
            )
            raise IngestionRecordError("scenario", scenario.scenario_id, exc) from exc
    db.flush()
    return counts


def _document_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def ingest_documents(
    db: Session,
    index: KnowledgeIndex,
    *,
    dataset_path: Path | None = None,
    batch_id: uuid.UUID | None = None,
) -> dict[str, int]:
    dataset = load_runbook_dataset(dataset_path)
    counts = {"documents": 0, "chunks": 0}
    for document in dataset.documents:
        try:
            with db.begin_nested():
                digest = _document_hash(document.body)
                doc_id = stable_uuid("knowledge", f"{document.source_key}:{document.version}")
                _upsert(
                    db,
                    KnowledgeDocument,
                    doc_id,
                    source_key=document.source_key,
                    document_type=document.document_type,
                    title=document.title,
                    body=document.body,
                    version=document.version,
                    license=SYNTHETIC_LICENSE,
                    source_url=None,
                    content_hash=digest,
                    metadata_json={
                        "service_key": document.service_key,
                        "error_codes": document.error_codes,
                        "tags": document.tags,
                        "synthetic": True,
                    },
                    active=True,
                    is_synthetic=True,
                )
                db.flush()
                chunk_count = index.upsert_document(
                    document,
                    license=SYNTHETIC_LICENSE,
                    source_url=None,
                    content_hash=digest,
                )
            counts["documents"] += 1
            counts["chunks"] += chunk_count
        except Exception as exc:
            logger.exception(
                "Knowledge document ingestion failed", extra={"source_key": document.source_key}
            )
            if batch_id is not None:
                db.flush()
            raise IngestionRecordError("knowledge_document", document.source_key, exc) from exc
    return counts


def run_ingestion(
    db: Session,
    index: KnowledgeIndex,
    *,
    scenario_path: Path | None = None,
    runbook_path: Path | None = None,
) -> dict[str, Any]:
    batch = IngestionBatch(pipeline="synthetic_seed", source_version="unresolved", status="running")
    db.add(batch)
    db.commit()
    try:
        dataset = load_scenario_dataset(scenario_path)
        batch.source_version = dataset.seed_version
        db.flush()
        scenario_counts = ingest_scenarios(db, dataset_path=scenario_path)
        db.commit()
        document_counts = ingest_documents(db, index, dataset_path=runbook_path, batch_id=batch.id)
        db.commit()
        vector_counts = index.count()
        batch.status = "completed"
        batch.counts = {**scenario_counts, **document_counts, "chroma": vector_counts}
        batch.completed_at = datetime.now(UTC)
        db.add(batch)
        db.commit()
        return {"batch_id": str(batch.id), "status": batch.status, **batch.counts}
    except Exception as exc:
        db.rollback()
        batch = db.get(IngestionBatch, batch.id)
        if batch:
            batch.status = "failed"
            batch.counts = {"error": type(exc).__name__}
            batch.completed_at = datetime.now(UTC)
            record_error = exc if isinstance(exc, IngestionRecordError) else None
            db.add(
                IngestionFailure(
                    batch_id=batch.id,
                    entity_type=record_error.entity_type if record_error else "pipeline",
                    entity_key=record_error.entity_key if record_error else "synthetic-seed",
                    error_type=type(record_error.cause if record_error else exc).__name__,
                    error_message=str(exc)[:2000],
                    retryable=True,
                    payload={},
                )
            )
            db.commit()
        raise


def record_counts(db: Session) -> dict[str, int]:
    from sqlalchemy import func

    from opspilot.db.models import (
        ActionExecution,
        ApplicationLog,
        Approval,
        Asset,
        AuditEvent,
        DemoSession,
        Incident,
        InvestigationFinding,
        InvestigationRun,
        KnowledgeDocument,
        ProposedAction,
        ResolutionRecord,
        WorkOrder,
    )

    models = {
        "demo_sessions": DemoSession,
        "incident_assets": IncidentAsset,
        "incidents": Incident,
        "assets": Asset,
        "application_logs": ApplicationLog,
        "work_orders": WorkOrder,
        "knowledge_documents": KnowledgeDocument,
        "investigation_runs": InvestigationRun,
        "investigation_findings": InvestigationFinding,
        "proposed_actions": ProposedAction,
        "approvals": Approval,
        "action_executions": ActionExecution,
        "resolution_records": ResolutionRecord,
        "audit_events": AuditEvent,
        "ingestion_batches": IngestionBatch,
        "ingestion_failures": IngestionFailure,
    }
    return {
        name: int(db.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in models.items()
    }
