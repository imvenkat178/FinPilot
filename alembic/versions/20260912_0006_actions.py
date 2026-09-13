"""Durable reviewed chat actions and execution receipts."""
from alembic import op
import sqlalchemy as sa

revision = '20260912_0006'
down_revision = '20260912_0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('action_proposals',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('household_id', sa.String(40), sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(40), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', sa.String(40), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('generation_id', sa.String(40), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('operations', sa.JSON(), nullable=False),
        sa.Column('preview', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_action_proposals_owner_conversation', 'action_proposals', ['household_id','user_id','conversation_id'])
    op.create_table('action_receipts',
        sa.Column('proposal_id', sa.String(40), sa.ForeignKey('action_proposals.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('proposal_version', sa.Integer(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('action_receipts')
    op.drop_index('ix_action_proposals_owner_conversation', table_name='action_proposals')
    op.drop_table('action_proposals')
