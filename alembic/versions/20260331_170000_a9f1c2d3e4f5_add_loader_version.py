"""add loader_version column

Revision ID: a9f1c2d3e4f5
Revises: b6_08_site_settings
Create Date: 2026-03-31 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9f1c2d3e4f5"
down_revision: str = "b6_08_site_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("loader_version", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("servers", "loader_version")
