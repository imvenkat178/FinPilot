"""Private document library with indexed lexical retrieval.

Revision ID: 20260912_0003
Revises: 20260911_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20260912_0003"
down_revision = "20260911_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("documents",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id", "household_id"], ["memberships.user_id", "memberships.household_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("household_id", "user_id", "sha256", name="uq_documents_owner_sha256"))
    op.create_index("ix_documents_owner_created", "documents", ["household_id", "user_id", "created_at"])
    op.create_table("document_chunks",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("document_id", sa.String(40), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_document_chunks_document_position", "document_chunks", ["document_id", "position"])
    op.create_table("document_terms",
        sa.Column("chunk_id", sa.String(40), nullable=False),
        sa.Column("term", sa.String(80), nullable=False),
        sa.Column("household_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id", "term"))
    op.create_index("ix_document_terms_owner_term", "document_terms", ["household_id", "user_id", "term"])


def downgrade():
    op.drop_index("ix_document_terms_owner_term", table_name="document_terms")
    op.drop_table("document_terms")
    op.drop_index("ix_document_chunks_document_position", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_owner_created", table_name="documents")
    op.drop_table("documents")
