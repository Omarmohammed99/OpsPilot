import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from opspilot.api.app import _recover_stale_investigations
from opspilot.config import Settings
from opspilot.db.models import AuditEvent, DemoSession, Incident, InvestigationRun
from opspilot.db.startup import startup_initialization_lock
from opspilot.retrieval.chroma_store import reset_ephemeral_chroma_directory


def test_vercel_runtime_defaults_to_ephemeral_chroma_and_secure_cookies(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("CHROMA_PERSIST_DIRECTORY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.chroma_persist_directory == Path(tempfile.gettempdir()) / "opspilot-chroma"
    assert settings.opspilot_env == "production"


def test_vercel_runtime_rejects_non_temporary_chroma_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("CHROMA_PERSIST_DIRECTORY", str(Path.cwd() / "persistent-chroma"))

    with pytest.raises(ValidationError, match="temporary directory"):
        Settings(_env_file=None)


def test_local_chroma_default_is_unchanged(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("CHROMA_PERSIST_DIRECTORY", raising=False)

    assert Settings(_env_file=None).chroma_persist_directory == Path("./data/chroma")


def test_chroma_rebuild_reset_is_confined_to_os_temp(tmp_path):
    index_path = tmp_path / "chroma" / "broken.db"
    index_path.parent.mkdir()
    index_path.write_text("corrupt", encoding="utf-8")

    reset_ephemeral_chroma_directory(index_path.parent)

    assert index_path.parent.is_dir()
    assert not index_path.exists()
    with pytest.raises(ValueError, match="outside the temporary directory"):
        reset_ephemeral_chroma_directory(Path.cwd() / "data" / "chroma")


def test_vercel_configuration_selects_container_and_vercel_port():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    dockerfile = Path("Dockerfile.vercel").read_text(encoding="utf-8")

    assert config["framework"] == "container"
    assert "VERCEL=1" in dockerfile
    assert "--host 0.0.0.0 --port ${PORT:-7860}" in dockerfile
    assert "data/seed/public" in dockerfile


def test_startup_lock_is_noop_for_local_sqlite():
    with startup_initialization_lock("sqlite:///local.db"):
        assert True


def test_postgres_startup_lock_releases_after_initialization():
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (True,)
    with patch("opspilot.db.startup.psycopg.connect") as connect:
        connect.return_value.__enter__.return_value = connection
        with startup_initialization_lock("postgresql+psycopg://user:pass@localhost/db"):
            pass

    assert connection.execute.call_count == 2
    assert "pg_advisory_unlock" in connection.execute.call_args_list[-1].args[0]


def test_startup_recovery_marks_only_expired_runs(db_factory):
    now = datetime.now(UTC)
    session_id = uuid4()
    incident_id = uuid4()
    old_id, active_id = uuid4(), uuid4()
    with db_factory() as db:
        db.add(DemoSession(id=session_id, is_template=True, seed_version="test-v1"))
        db.add(
            Incident(
                id=incident_id,
                demo_session_id=session_id,
                external_key="INC-TEST",
                title="Test incident",
                description="Synthetic incident for the stale-run recovery test.",
                severity="medium",
                status="open",
                service_key="test-service",
                opened_at=now,
            )
        )
        db.flush()
        db.add_all(
            [
                InvestigationRun(
                    id=old_id,
                    demo_session_id=session_id,
                    incident_id=incident_id,
                    thread_id="old-run",
                    status="running",
                    request_text="Investigate the old synthetic run.",
                    started_at=now - timedelta(minutes=5),
                ),
                InvestigationRun(
                    id=active_id,
                    demo_session_id=session_id,
                    incident_id=incident_id,
                    thread_id="active-run",
                    status="running",
                    request_text="Investigate the still-active synthetic run.",
                    started_at=now - timedelta(seconds=15),
                ),
            ]
        )
        db.commit()

    assert _recover_stale_investigations(db_factory, stale_after=120) == 1
    with db_factory() as db:
        assert db.get(InvestigationRun, old_id).status == "interrupted"
        assert db.get(InvestigationRun, active_id).status == "running"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
