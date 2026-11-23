"""Operational CLI for migrations, ingestion and data-layer verification."""

import json
import logging
import os
from pathlib import Path
from typing import Annotated

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from opspilot.config import get_settings
from opspilot.db.checkpoint import setup_checkpoint_tables
from opspilot.db.session import SessionLocal, engine
from opspilot.ingestion.pipeline import record_counts, run_ingestion
from opspilot.retrieval.chroma_store import KnowledgeIndex

app = typer.Typer(no_args_is_help=True, help="OpsPilot data-layer tools")
logger = logging.getLogger("opspilot")


def _alembic_config() -> Config:
    configured_root = os.getenv("OPSPILOT_PROJECT_DIR")
    root = Path(configured_root) if configured_root else Path.cwd()
    if not (root / "alembic.ini").exists():
        root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", get_settings().safe_database_url.replace("%", "%%"))
    return config


@app.command()
def migrate() -> None:
    """Apply all Alembic migrations to the configured PostgreSQL database."""
    command.upgrade(_alembic_config(), "head")
    typer.echo("Database migrations are current.")


@app.command()
def db_check() -> None:
    """Check the configured database connection without displaying its URL."""
    with engine.connect() as connection:
        version = connection.scalar(text("select current_setting('server_version')"))
    typer.echo(json.dumps({"postgres": "ok", "server_version": version}))


@app.command()
def ingest(
    scenarios: Annotated[Path | None, typer.Option(help="Optional scenario JSON path")] = None,
    runbooks: Annotated[Path | None, typer.Option(help="Optional runbook JSON path")] = None,
) -> None:
    """Upsert deterministic public scenarios, canonical docs, and Chroma chunks."""
    logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO))
    command.upgrade(_alembic_config(), "head")
    index = KnowledgeIndex()
    with SessionLocal() as db:
        summary = run_ingestion(db, index, scenario_path=scenarios, runbook_path=runbooks)
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@app.command()
def counts() -> None:
    """Print actual relational and Chroma record counts."""
    with SessionLocal() as db:
        result = record_counts(db)
    result["chroma"] = KnowledgeIndex().count()
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@app.command()
def verify() -> None:
    """Run connectivity and retrieval smoke checks against ingested data."""
    index = KnowledgeIndex()
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    matches = index.retrieve(
        "connection pool active idle acquisition timeout", service_key="checkout-api", limit=5
    )
    if not matches or not any("pool" in item["metadata"].get("source_key", "") for item in matches):
        typer.echo(
            "Retrieval verification failed: expected database-pool runbook was not retrieved",
            err=True,
        )
        raise typer.Exit(code=1)
    with SessionLocal() as db:
        relational = record_counts(db)
    typer.echo(
        json.dumps(
            {"database": "ok", "retrieval": "ok", "matches": matches[:2], "counts": relational},
            indent=2,
        )
    )


@app.command()
def checkpoint_setup() -> None:
    """Create or verify LangGraph PostgresSaver tables using its supported setup."""
    setup_checkpoint_tables()
    typer.echo("LangGraph PostgreSQL checkpoint tables are ready.")


@app.command()
def health() -> None:
    """Check PostgreSQL and Chroma local service health."""
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    vector_health = KnowledgeIndex().health()
    typer.echo(json.dumps({"postgres": "ok", "chroma": vector_health}, indent=2))


if __name__ == "__main__":
    app()
