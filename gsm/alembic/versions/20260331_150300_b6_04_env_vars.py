"""add_env_vars

Revision ID: b6_04_envvar
Revises: b6_03_update
Create Date: 2026-03-31 15:03:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6_04_envvar"
down_revision: Union[str, None] = "b6_03_update"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("environment_vars", sa.Text(), server_default="{}", nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_column("environment_vars")
