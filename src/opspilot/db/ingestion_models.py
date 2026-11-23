"""Pipeline provenance and recoverable per-record failures."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opspilot.db.base import Base
from opspilot.db.models import now_utc

JsonType = JSON().with_variant(JSONB, "postgresql")


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','completed','failed','partial')", name="ingestion_status_value"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pipeline: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    counts: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionFailure(Base):
    __tablename__ = "ingestion_failures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(240), nullable=False)
    error_type: Mapped[str] = mapped_column(String(120), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = mapped_column(nullable=False, default=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
