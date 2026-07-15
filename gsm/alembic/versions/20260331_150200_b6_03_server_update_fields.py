"""add_server_update_fields

Revision ID: b6_03_update
Revises: b6_02_rbac
Create Date: 2026-03-31 15:02:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6_03_update"
down_revision: Union[str, None] = "b6_02_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_update_server", sa.Boolean(), server_default="0", nullable=False
            )
        )
        batch_op.add_column(
            sa.Column("last_server_update", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("latest_known_version", sa.String(length=100), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_column("latest_known_version")
        batch_op.drop_column("last_server_update")
        batch_op.drop_column("auto_update_server")
