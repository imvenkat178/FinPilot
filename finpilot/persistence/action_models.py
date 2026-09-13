"""User-owned review proposals and atomic execution receipts."""
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base, utcnow


class ActionProposalRow(Base):
    __tablename__ = 'action_proposals'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey('households.id', ondelete='CASCADE'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'))
    generation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default='proposed')
    operations: Mapped[list] = mapped_column(JSON)
    preview: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index('ix_action_proposals_owner_conversation', 'household_id', 'user_id', 'conversation_id'),)


class ActionReceiptRow(Base):
    __tablename__ = 'action_receipts'
    # One execution identity per proposal, enforced across processes.
    proposal_id: Mapped[str] = mapped_column(ForeignKey('action_proposals.id', ondelete='CASCADE'), primary_key=True)
    proposal_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowStateRow(Base):
    __tablename__ = 'workflow_states'
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(40))
    state: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActionAttachmentRow(Base):
    __tablename__ = 'action_attachments'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey('households.id', ondelete='CASCADE'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'))
    filename: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index('ix_action_attachments_owner', 'household_id','user_id','conversation_id'),)
