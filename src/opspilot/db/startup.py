"""PostgreSQL coordination for safe, repeatable serverless startup initialization."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy.engine import make_url

_LOCK_NAMESPACE = 0x4F505350  # "OPSP"
_LOCK_ID = 1


@contextmanager
def startup_initialization_lock(
    database_url: str, *, timeout_seconds: float = 120
) -> Iterator[None]:
    """Serialize migrations, checkpoint DDL, and seed ingestion across app instances."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        # Keep SQLite-based local development and tests independent of PostgreSQL.
        yield
        return

    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as connection:
        deadline = time.monotonic() + timeout_seconds
        while not connection.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", (_LOCK_NAMESPACE, _LOCK_ID)
        ).fetchone()[0]:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for application startup initialization lock")
            time.sleep(0.2)
        try:
            yield
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s, %s)", (_LOCK_NAMESPACE, _LOCK_ID))
