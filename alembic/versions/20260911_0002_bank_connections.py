"""Encrypted Plaid connections and atomic incremental-sync cursors.

Revision ID: 20260911_0002
Revises: 20260911_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20260911_0002"
down_revision = "20260911_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("bank_connections",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("item_id", sa.String(200), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("institution_id", sa.String(100), nullable=False),
        sa.Column("institution_name", sa.String(150), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("cursor", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("account_mapping", sa.JSON(), nullable=False),
        sa.Column("notices", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("household_id", "environment", "item_id",
                            name="uq_bank_connections_household_item"))
    op.create_index("ix_bank_connections_household_id", "bank_connections", ["household_id"])


def downgrade():
    op.drop_index("ix_bank_connections_household_id", table_name="bank_connections")
    op.drop_table("bank_connections")
