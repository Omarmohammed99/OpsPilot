from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from opspilot.db import ingestion_models as _ingestion_models  # noqa: F401
from opspilot.db import models as _models  # noqa: F401
from opspilot.db.base import Base


@pytest.fixture
def db_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    database_path = tmp_path / "opspilot-test.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    engine.dispose()


@pytest.fixture
def chroma_path(tmp_path):
    return tmp_path / "chroma"
