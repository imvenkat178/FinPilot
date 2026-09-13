"""Migration round-trip contracts against disposable databases only."""
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from finpilot.persistence.database import Base

ROOT = Path(__file__).resolve().parents[1]


def migration_config(url):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_initial_migration_roundtrip_matches_runtime_schema(tmp_path, monkeypatch):
    monkeypatch.delenv("FINPILOT_DATABASE_URL", raising=False)
    monkeypatch.delenv("FINPILOT_ENV", raising=False)
    url = "sqlite:///" + (tmp_path / "migrations.db").as_posix()
    config = migration_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260912_0003"
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
        command.upgrade(config, "head")  # Repeated release jobs are a no-op at head.
        command.downgrade(config, "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        command.upgrade(config, "head")
        assert "households" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_initial_migration_compiles_for_postgresql_without_a_server(monkeypatch):
    monkeypatch.delenv("FINPILOT_DATABASE_URL", raising=False)
    monkeypatch.delenv("FINPILOT_ENV", raising=False)
    config = migration_config("postgresql+psycopg://example@db.test/finpilot")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE households" in sql
    assert "snapshot JSON NOT NULL" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "CREATE INDEX ix_transactions_household_account_date" in sql
    assert "CREATE UNIQUE INDEX ix_users_email" in sql
    assert "ON DELETE CASCADE" in sql
