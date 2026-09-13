"""Durable private conversations and revision-bound action proposals.

Revision ID: 20260912_0003
Revises: 20260911_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20260912_0003"
down_revision = "20260911_0002"
branch_labels = None
depends_on = None


def tenant_columns():
    return [sa.Column("household_id", sa.String(40), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)]


def upgrade():
    op.create_table("conversations",
        sa.Column("id", sa.String(40), primary_key=True), *tenant_columns(),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_conversations_household_id", "conversations", ["household_id"])
    op.create_table("conversation_turns",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_message_id", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "client_message_id", name="uq_conversation_client_message"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_sequence"))
    op.create_table("action_proposals",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        *tenant_columns(),
        sa.Column("workspace_revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_action_proposals_household_id", "action_proposals", ["household_id"])


def downgrade():
    op.drop_index("ix_action_proposals_household_id", table_name="action_proposals")
    op.drop_table("action_proposals")
    op.drop_table("conversation_turns")
    op.drop_index("ix_conversations_household_id", table_name="conversations")
    op.drop_table("conversations")
