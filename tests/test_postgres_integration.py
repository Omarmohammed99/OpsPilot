"""Optional real-PostgreSQL integration tests using a dedicated disposable test database."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from opspilot.db.base import Base
from opspilot.db.models import ApplicationLog, Asset, Incident, KnowledgeDocument, WorkOrder
from opspilot.ingestion.pipeline import run_ingestion
from opspilot.retrieval.chroma_store import KnowledgeIndex
from opspilot.retrieval.embeddings import LocalHashEmbeddingFunction
from opspilot.services.demo_sessions import create_demo_session

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def postgres_test_db():
    url = os.getenv("OPSPILOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set OPSPILOT_TEST_DATABASE_URL to an isolated test database")
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(url, pool_pre_ping=True)
    managed_tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {managed_tables} RESTART IDENTITY CASCADE"))
    yield engine
    engine.dispose()


def test_postgres_connectivity_and_migrated_schema(postgres_test_db):
    with postgres_test_db.connect() as connection:
        assert (
            connection.scalar(text("select current_setting('server_version_num')::int")) >= 160000
        )
    table_names = set(inspect(postgres_test_db).get_table_names())
    assert {
        "incidents",
        "assets",
        "application_logs",
        "work_orders",
        "approvals",
        "action_executions",
        "audit_events",
    } <= table_names


def test_full_ingestion_repeat_and_demo_session_isolation(postgres_test_db, tmp_path):
    index = KnowledgeIndex(
        tmp_path / "pg-chroma", prefix="pg-it", embedding_function=LocalHashEmbeddingFunction()
    )
    with Session(postgres_test_db, expire_on_commit=False, autoflush=False) as db:
        first = run_ingestion(db, index)
        second = run_ingestion(db, index)
        assert first["status"] == second["status"] == "completed"
        assert index.count() == {"troubleshooting": 7, "error_refs": 1}
        assert db.scalar(select(func.count()).select_from(Incident)) == 8
        assert db.scalar(select(func.count()).select_from(Asset)) == 8
        assert db.scalar(select(func.count()).select_from(ApplicationLog)) == 24
        assert db.scalar(select(func.count()).select_from(WorkOrder)) == 7
        assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 8
        session_a, token_a = create_demo_session(db, "db-pool-exhaustion")
        session_b, token_b = create_demo_session(db, "db-pool-exhaustion")
        db.commit()
        assert token_a != token_b
        incident_a = db.scalar(select(Incident).where(Incident.demo_session_id == session_a.id))
        incident_b = db.scalar(select(Incident).where(Incident.demo_session_id == session_b.id))
        assert incident_a.id != incident_b.id
