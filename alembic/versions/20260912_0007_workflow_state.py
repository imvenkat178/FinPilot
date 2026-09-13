"""Persist pending clarification independently from language history."""
from alembic import op
import sqlalchemy as sa
revision = "20260912_0007"
down_revision = "20260912_0006"
branch_labels = depends_on = None
def upgrade():
    op.create_table("workflow_states",
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("generation_id", sa.String(40), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
def downgrade():
    op.drop_table("workflow_states")
