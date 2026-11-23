"""Setup helper for the official LangGraph PostgreSQL checkpointer schema."""

from opspilot.config import get_settings


def setup_checkpoint_tables() -> None:
    try:
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
        from sqlalchemy.engine import make_url
    except ImportError as exc:
        raise RuntimeError("Install the checkpoint extra with: uv sync --extra checkpoint") from exc
    pg_url = make_url(get_settings().safe_database_url).set(drivername="postgresql")
    with psycopg.connect(
        pg_url.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        saver = PostgresSaver(connection)
        saver.setup()
