"""Private, user-owned source documents and indexed lexical retrieval postings."""
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base, utcnow


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(30), default="upload")
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "household_id"], ["memberships.user_id", "memberships.household_id"], ondelete="CASCADE"),
        UniqueConstraint("household_id", "user_id", "sha256", name="uq_documents_owner_sha256"),
        Index("ix_documents_owner_created", "household_id", "user_id", "created_at"),
    )


class DocumentChunkRow(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (Index("ix_document_chunks_document_position", "document_id", "position"),)


class DocumentTermRow(Base):
    __tablename__ = "document_terms"
    chunk_id: Mapped[str] = mapped_column(ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True)
    term: Mapped[str] = mapped_column(String(80), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40))
    frequency: Mapped[int] = mapped_column(Integer)
    __table_args__ = (Index("ix_document_terms_owner_term", "household_id", "user_id", "term"),)
