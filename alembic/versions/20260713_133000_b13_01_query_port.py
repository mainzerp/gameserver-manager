"""Add query_port to servers

Revision ID: b13_01_query_port
Revises: b12_02_fk_cascades_indexes
Create Date: 2026-07-13 13:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b13_01_query_port"
down_revision: Union[str, None] = "b12_02_fk_cascades_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.add_column("servers", sa.Column("query_port", sa.Integer(), nullable=True))

    # Populate query_port for existing Steam servers: game port + 1
    # This keeps existing behavior and avoids NULL collisions in the port manager.
    if dialect == "sqlite":
        op.execute("UPDATE servers SET query_port = port + 1 WHERE server_type = 'steam' AND query_port IS NULL")
    else:
        op.execute(
            sa.text("UPDATE servers SET query_port = port + 1 WHERE server_type = 'steam' AND query_port IS NULL")
        )


def downgrade() -> None:
    op.drop_column("servers", "query_port")
