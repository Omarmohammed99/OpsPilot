from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_alembic_migration_up_and_down(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "migration-test.sqlite"
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "8ac50f53cbb9")
    engine = create_engine(f"sqlite:///{db_path}")
    table_names = set(inspect(engine).get_table_names())
    assert {
        "incidents",
        "assets",
        "application_logs",
        "work_orders",
        "approvals",
        "action_executions",
        "audit_events",
    } <= table_names
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "8ac50f53cbb9")
    assert "incidents" in inspect(engine).get_table_names()
    engine.dispose()
