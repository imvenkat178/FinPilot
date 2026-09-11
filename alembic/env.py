"""Run reviewed relational migrations without importing the application server."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from finpilot.persistence.database import Base

config = context.config
target_metadata = Base.metadata


def database_url():
    value = os.getenv("FINPILOT_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql+psycopg://", 1)
    elif value.startswith("postgresql://"):
        value = value.replace("postgresql://", "postgresql+psycopg://", 1)
    if os.getenv("FINPILOT_ENV") == "production" and not value.startswith("postgresql"):
        raise RuntimeError("Hosted production migrations require PostgreSQL.")
    return value


def run_migrations_offline():
    context.configure(url=database_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"},
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    url = database_url()
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata,
                              compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
