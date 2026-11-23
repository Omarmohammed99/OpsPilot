from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from opspilot.db.models import DemoSession, Incident


def test_invalid_severity_and_duplicate_external_key_are_rejected(db_factory):
    with db_factory() as db:
        session = DemoSession(
            is_template=True, token_hash=None, seed_version="test", status="active"
        )
        db.add(session)
        db.flush()
        incident = Incident(
            demo_session_id=session.id,
            external_key="INC-1",
            title="One",
            description="description",
            severity="impossible",
            status="open",
            service_key="svc",
            opened_at=datetime.now(UTC),
        )
        db.add(incident)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
