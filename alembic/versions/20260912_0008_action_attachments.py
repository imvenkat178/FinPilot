"""Private immutable CSV attachments for reviewed imports."""
from alembic import op
import sqlalchemy as sa
revision = "20260912_0008"
down_revision = "20260912_0007"
branch_labels = depends_on = None
def upgrade():
    op.create_table("action_attachments",
        sa.Column("id",sa.String(40),primary_key=True),
        sa.Column("household_id",sa.String(40),sa.ForeignKey("households.id",ondelete="CASCADE"),nullable=False),
        sa.Column("user_id",sa.String(40),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("conversation_id",sa.String(40),sa.ForeignKey("conversations.id",ondelete="CASCADE"),nullable=False),
        sa.Column("filename",sa.String(160),nullable=False),
        sa.Column("payload",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_action_attachments_owner","action_attachments",["household_id","user_id","conversation_id"])
def downgrade():
    op.drop_table("action_attachments")
