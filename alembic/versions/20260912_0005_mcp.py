"""Private MCP connections and revocable read grants."""
from alembic import op
import sqlalchemy as sa

revision = "20260912_0005"
down_revision = "20260912_0004"
branch_labels = None
depends_on = None


def upgrade():
    for table, columns in (
        ("mcp_tokens", [sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
                        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False)]),
        ("mcp_connections", [sa.Column("server_id", sa.String(80), nullable=False),
                             sa.Column("encrypted_token", sa.Text(), nullable=False)]),
    ):
        op.create_table(table,
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("household_id", sa.String(40), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            *columns)
        op.create_index("ix_" + table + "_owner", table, ["household_id", "user_id"])


def downgrade():
    for table in ("mcp_connections", "mcp_tokens"):
        op.drop_index("ix_" + table + "_owner", table_name=table)
        op.drop_table(table)
