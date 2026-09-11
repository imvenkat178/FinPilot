"""Persist users, workspaces, sessions, audit history, and transaction indexes.

Revision ID: 20260911_0001
Revises: None
"""
from alembic import op
import sqlalchemy as sa

revision = "20260911_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table("households",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"))

    op.create_table("memberships",
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "household_id"))

    op.create_table("sessions",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"))
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table("audit_events",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(40), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_audit_events_household_id", "audit_events", ["household_id"])

    op.create_table("assistant_answers",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_assistant_answers_household_id", "assistant_answers", ["household_id"])

    op.create_table("transactions",
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("id", sa.String(100), nullable=False),
        sa.Column("account_id", sa.String(100), nullable=False),
        sa.Column("posted_on", sa.String(10), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("household_id", "id"))
    op.create_index("ix_transactions_household_account_date", "transactions",
                    ["household_id", "account_id", "posted_on"])

    op.create_table("rate_limits",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"))


def downgrade():
    # This initial downgrade removes stored data; use it only on disposable databases.
    op.drop_table("rate_limits")
    op.drop_index("ix_transactions_household_account_date", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_assistant_answers_household_id", table_name="assistant_answers")
    op.drop_table("assistant_answers")
    op.drop_index("ix_audit_events_household_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("memberships")
    op.drop_table("households")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
