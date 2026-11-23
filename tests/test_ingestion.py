import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from opspilot.db.ingestion_models import IngestionBatch, IngestionFailure
from opspilot.db.models import (
    ApplicationLog,
    Asset,
    DemoSession,
    Incident,
    KnowledgeDocument,
    WorkOrder,
)
from opspilot.ingestion.pipeline import run_ingestion
from opspilot.retrieval.chroma_store import KnowledgeIndex
from opspilot.retrieval.embeddings import LocalHashEmbeddingFunction
from opspilot.services.demo_sessions import create_demo_session, resolve_demo_session


def make_index(path):
    return KnowledgeIndex(
        path, prefix="ingestion-test", embedding_function=LocalHashEmbeddingFunction()
    )


def test_ingestion_counts_idempotency_and_retrieval(db_factory, chroma_path):
    index = make_index(chroma_path)
    with db_factory() as db:
        first = run_ingestion(db, index)
        second = run_ingestion(db, index)
        assert first["status"] == second["status"] == "completed"
        assert first["scenarios"] == 8
        assert first["documents"] == 8
        assert first["logs"] == 24
        assert first["work_orders"] == 7
        assert index.count() == {"troubleshooting": 7, "error_refs": 1}
        assert db.scalar(select(func.count()).select_from(Incident)) == 8
        assert db.scalar(select(func.count()).select_from(Asset)) == 8
        assert db.scalar(select(func.count()).select_from(ApplicationLog)) == 24
        assert db.scalar(select(func.count()).select_from(WorkOrder)) == 7
        assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 8
        assert db.scalar(select(func.count()).select_from(DemoSession)) == 1
        assert db.scalar(select(func.count()).select_from(IngestionBatch)) == 2
        assert db.scalar(select(func.count()).select_from(IngestionFailure)) == 0

        incident = db.scalar(select(Incident).where(Incident.external_key == "INC-DEMO-DB-001"))
        assert incident is not None
        assert incident.demo_session.is_template
        matches = index.retrieve(
            "connection pool acquisition waits active idle", service_key="checkout-api"
        )
        assert matches and matches[0]["metadata"]["source_key"] == "runbook.db-pool"


def test_each_demo_session_gets_isolated_records(db_factory, chroma_path):
    with db_factory() as db:
        run_ingestion(db, make_index(chroma_path))
        first_session, first_token = create_demo_session(db, "db-pool-exhaustion")
        db.commit()
        second_session, second_token = create_demo_session(db, "db-pool-exhaustion")
        db.commit()
        assert first_token != second_token
        assert first_session.token_hash != first_token
        assert first_session.token_hash != second_session.token_hash
        assert resolve_demo_session(db, first_token).id == first_session.id
        first_incident = db.scalar(
            select(Incident).where(Incident.demo_session_id == first_session.id)
        )
        second_incident = db.scalar(
            select(Incident).where(Incident.demo_session_id == second_session.id)
        )
        assert first_incident.id != second_incident.id
        assert first_incident.external_key == second_incident.external_key
        assert len(first_incident.incident_assets) == len(second_incident.incident_assets) == 1
        assert len(first_incident.incident_assets[0].asset.logs) == 3


def test_invalid_session_token_is_rejected(db_factory):
    from opspilot.services.demo_sessions import InvalidDemoSession

    with db_factory() as db, pytest.raises(InvalidDemoSession):
        resolve_demo_session(db, "not-a-valid-token")


def test_failed_document_ingestion_is_recorded_and_retry_is_safe(db_factory, chroma_path, tmp_path):
    bad_runbooks = tmp_path / "bad-runbooks.json"
    bad_runbooks.write_text(json.dumps({"documents": [{"source_key": "broken"}]}), encoding="utf-8")
    with db_factory() as db:
        with pytest.raises(Exception, match="documents|validation"):
            run_ingestion(db, make_index(chroma_path), runbook_path=bad_runbooks)
        failed_batch = db.scalar(select(IngestionBatch).where(IngestionBatch.status == "failed"))
        assert failed_batch is not None
        assert db.scalar(select(func.count()).select_from(IngestionFailure)) == 1
        # Scenario rows committed before the document stage and remain safe to rerun.
        assert db.scalar(select(func.count()).select_from(Incident)) == 8
        success = run_ingestion(db, make_index(chroma_path))
        assert success["status"] == "completed"
        assert db.scalar(select(func.count()).select_from(Incident)) == 8


def test_invalid_input_creates_failed_batch_and_record(db_factory, chroma_path, tmp_path):
    malformed = tmp_path / "bad-scenarios.json"
    malformed.write_text('{"seed_version":"broken","seed":1,"scenarios":[{}]}', encoding="utf-8")
    with db_factory() as db:
        with pytest.raises(ValidationError):
            run_ingestion(db, make_index(chroma_path), scenario_path=malformed)
        batch = db.scalar(select(IngestionBatch).where(IngestionBatch.status == "failed"))
        assert batch is not None
        failure = db.scalar(select(IngestionFailure).where(IngestionFailure.batch_id == batch.id))
        assert failure is not None
        assert failure.entity_type == "pipeline"
