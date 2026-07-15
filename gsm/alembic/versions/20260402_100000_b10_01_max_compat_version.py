"""add max_compatible_mc_version fields

Revision ID: b10_01_max_compat_version
Revises: b9_05_batch6_extras
Create Date: 2026-04-02 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b10_01_max_compat_version"
down_revision: str = "b9_05_batch6_extras"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers", sa.Column("max_compatible_mc_version", sa.String(20), nullable=True)
    )
    op.add_column(
        "servers",
        sa.Column(
            "max_compatible_checked_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("servers", "max_compatible_checked_at")
    op.drop_column("servers", "max_compatible_mc_version")
