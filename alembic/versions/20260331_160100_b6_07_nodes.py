"""add_nodes_table_and_server_node_id

Revision ID: b6_07_nodes
Revises: b6_06_webauthn
Create Date: 2026-03-31 16:01:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6_07_nodes"
down_revision: Union[str, None] = "b6_06_webauthn"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("api_url", sa.String(500), nullable=False),
        sa.Column("auth_token", sa.String(255), nullable=False),
        sa.Column("is_local", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="unknown", nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cpu_cores", sa.Integer(), nullable=True),
        sa.Column("ram_total_mb", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("node_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_servers_node_id", "nodes", ["node_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_constraint("fk_servers_node_id", type_="foreignkey")
        batch_op.drop_column("node_id")
    op.drop_table("nodes")
