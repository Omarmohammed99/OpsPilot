from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from opspilot.config import get_settings


def make_engine(url: str | None = None, *, pool_pre_ping: bool = True) -> Engine:
    url = url or get_settings().safe_database_url
    options: dict[str, object] = {"pool_pre_ping": pool_pre_ping, "pool_size": 5, "max_overflow": 5}
    if url.startswith("sqlite"):
        options = {"pool_pre_ping": pool_pre_ping}
    return create_engine(url, **options)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
