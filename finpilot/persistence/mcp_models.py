"""User-owned MCP grants and encrypted external connections."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow


class MCPTokenRow(Base):
    __tablename__ = "mcp_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_mcp_tokens_owner", "household_id", "user_id"),)


class MCPConnectionRow(Base):
    __tablename__ = "mcp_connections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    server_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(100))
    encrypted_token: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_mcp_connections_owner", "household_id", "user_id"),)
