"""Opaque session authorization and isolated scenario cloning."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.datasets.loaders import load_scenario_dataset
from opspilot.db.models import (
    ApplicationLog,
    Asset,
    AuditEvent,
    DemoSession,
    Incident,
    IncidentAsset,
    WorkOrder,
)
from opspilot.ingestion.pipeline import TEMPLATE_SESSION_ID


class InvalidDemoSession(ValueError):
    pass


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_demo_session(
    db: Session, scenario_id: str, *, ttl_hours: int = 72
) -> tuple[DemoSession, str]:
    """Clone one seed scenario into a visitor scope; return raw cookie token once."""
    scenario_dataset = load_scenario_dataset()
    scenario = next(
        (item for item in scenario_dataset.scenarios if item.scenario_id == scenario_id), None
    )
    if scenario is None:
        raise ValueError(f"Unknown synthetic scenario: {scenario_id}")
    template_incident = db.scalar(
        select(Incident).where(
            Incident.demo_session_id == TEMPLATE_SESSION_ID,
            Incident.external_key == scenario.incident.external_key,
        )
    )
    if template_incident is None:
        raise RuntimeError("Seed scenario has not been ingested; run the ingestion command first")
    template_asset_id = db.scalar(
        select(Asset.id).where(
            Asset.demo_session_id == TEMPLATE_SESSION_ID,
            Asset.asset_key == scenario.asset.asset_key,
        )
    )
    if template_asset_id is None:
        raise RuntimeError(f"Seed asset for {scenario_id} is missing")
    template_link = db.get(IncidentAsset, (template_incident.id, template_asset_id))
    if template_link is None:
        raise RuntimeError(f"Seed scenario {scenario_id} has no linked asset")
    template_asset = db.get(Asset, template_link.asset_id)
    if template_asset is None:
        raise RuntimeError("Seed asset is missing")

    raw_token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=ttl_hours)
    demo_session = DemoSession(
        id=uuid.uuid4(),
        token_hash=hash_session_token(raw_token),
        is_template=False,
        seed_version=scenario_dataset.seed_version,
        created_at=datetime.now(UTC),
        expires_at=expires,
        status="active",
    )
    db.add(demo_session)
    db.flush()

    incident = Incident(
        demo_session_id=demo_session.id,
        external_key=template_incident.external_key,
        title=template_incident.title,
        description=template_incident.description,
        severity=template_incident.severity,
        status=template_incident.status,
        service_key=template_incident.service_key,
        opened_at=template_incident.opened_at,
        updated_at=template_incident.updated_at,
        is_synthetic=True,
    )
    asset = Asset(
        demo_session_id=demo_session.id,
        asset_key=template_asset.asset_key,
        service_key=template_asset.service_key,
        environment=template_asset.environment,
        asset_type=template_asset.asset_type,
        hostname_label=template_asset.hostname_label,
        operational_state=template_asset.operational_state,
        attributes=dict(template_asset.attributes),
        is_synthetic=True,
    )
    db.add_all([incident, asset])
    db.flush()
    db.add(IncidentAsset(incident_id=incident.id, asset_id=asset.id, relationship_role="affected"))

    for original in db.scalars(
        select(ApplicationLog).where(ApplicationLog.asset_id == template_asset.id)
    ):
        db.add(
            ApplicationLog(
                demo_session_id=demo_session.id,
                asset_id=asset.id,
                service_key=original.service_key,
                occurred_at=original.occurred_at,
                level=original.level,
                source=original.source,
                message=original.message,
                event_code=original.event_code,
                attributes=dict(original.attributes),
                is_synthetic=True,
            )
        )
    for original in db.scalars(
        select(WorkOrder).where(WorkOrder.incident_id == template_incident.id)
    ):
        db.add(
            WorkOrder(
                demo_session_id=demo_session.id,
                incident_id=incident.id,
                asset_id=asset.id,
                order_type=original.order_type,
                status=original.status,
                summary=original.summary,
                opened_at=original.opened_at,
                closed_at=original.closed_at,
                is_synthetic=True,
            )
        )
    db.add(
        AuditEvent(
            demo_session_id=demo_session.id,
            incident_id=incident.id,
            actor_id="visitor",
            event_type="demo_session_created",
            details={"scenario_id": scenario_id, "seed_version": scenario_dataset.seed_version},
        )
    )
    db.flush()
    return demo_session, raw_token


def resolve_demo_session(db: Session, raw_token: str) -> DemoSession:
    demo_session = db.scalar(
        select(DemoSession).where(DemoSession.token_hash == hash_session_token(raw_token))
    )
    now = datetime.now(UTC)
    if demo_session is None or demo_session.is_template or demo_session.status != "active":
        raise InvalidDemoSession("Invalid or expired demo session")
    expires_at = demo_session.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is None or expires_at <= now:
        if demo_session.status != "expired":
            demo_session.status = "expired"
            db.commit()
        raise InvalidDemoSession("Invalid or expired demo session")
    return demo_session
