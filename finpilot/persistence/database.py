"""Relational identity, tenant boundaries, sessions, audit, and domain snapshots.

PostgreSQL is the hosted backend. SQLite supports local development/tests.
Each unit of work owns its Session; no ORM Session is shared across requests.
"""
from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy import (JSON, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HouseholdRow(Base):
    __tablename__ = "households"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    snapshot: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MembershipRow(Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")


class SessionRow(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuditRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatRow(Base):
    __tablename__ = "assistant_answers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TransactionRow(Base):
    """Indexed read projection; authoritative posting commits with its snapshot."""
    __tablename__ = "transactions"
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(100))
    posted_on: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_transactions_household_account_date", "household_id", "account_id", "posted_on"),)


class BankConnectionRow(Base):
    __tablename__ = "bank_connections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(String(200))
    environment: Mapped[str] = mapped_column(String(20))
    institution_id: Mapped[str] = mapped_column(String(100), default="")
    institution_name: Mapped[str] = mapped_column(String(150), default="Linked bank")
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="active")
    account_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    notices: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("household_id", "environment", "item_id",
                                      name="uq_bank_connections_household_item"),)


class RateLimitRow(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class ConversationRow(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationTurnRow(Base):
    __tablename__ = "conversation_turns"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    client_message_id: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_message_id", name="uq_conversation_client_message"),
        UniqueConstraint("conversation_id", "sequence", name="uq_conversation_sequence"),
    )


class ActionProposalRow(Base):
    __tablename__ = "action_proposals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    workspace_revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    preview: Mapped[list] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="pending")
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Database:
    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("FINPILOT_DATABASE_URL", "sqlite:///./.local/finpilot.db")
        if self.url.startswith("postgres://"):
            self.url = self.url.replace("postgres://", "postgresql+psycopg://", 1)
        elif self.url.startswith("postgresql://"):
            self.url = self.url.replace("postgresql://", "postgresql+psycopg://", 1)
        options = {"pool_pre_ping": True}
        if self.url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if ":memory:" in self.url:
                options["poolclass"] = StaticPool
            elif self.url.startswith("sqlite:///"):
                Path(self.url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        else:
            options.update(pool_size=5, max_overflow=10, pool_timeout=10)
        self.engine = create_engine(self.url, **options)
        if self.url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=15000")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)

    def close(self):
        self.engine.dispose()
