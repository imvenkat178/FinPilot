"""Private conversation threads and durable generation lifecycle."""
from alembic import op
import sqlalchemy as sa
revision = '20260912_0004'
down_revision = '20260912_0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('conversations',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('household_id', sa.String(40), sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(40), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('context', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('active_generation', sa.String(40), nullable=True),
        sa.Column('busy_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_conversations_owner_updated', 'conversations', ['household_id','user_id','updated_at'])
    op.create_table('conversation_generations',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('conversation_id', sa.String(40), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_generations_conversation_created','conversation_generations',['conversation_id','created_at'])


def downgrade():
    op.drop_table('conversation_generations')
    op.drop_table('conversations')
