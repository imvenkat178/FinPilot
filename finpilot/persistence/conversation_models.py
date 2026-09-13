"""Private, durable conversations and addressable generation records."""
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base, utcnow


class ConversationRow(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey('households.id', ondelete='CASCADE'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    title: Mapped[str] = mapped_column(String(120))
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    active_generation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index('ix_conversations_owner_updated', 'household_id', 'user_id', 'updated_at'),)


class GenerationRow(Base):
    __tablename__ = 'conversation_generations'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index('ix_generations_conversation_created', 'conversation_id', 'created_at'),)
