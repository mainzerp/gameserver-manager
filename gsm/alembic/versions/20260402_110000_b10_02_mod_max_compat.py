"""add max_compatible_mc_version to mods

Revision ID: b10_02_mod_max_compat
Revises: b10_01_max_compat_version
Create Date: 2026-04-02 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b10_02_mod_max_compat"
down_revision: Union[str, None] = "b10_01_max_compat_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mods", sa.Column("max_compatible_mc_version", sa.String(20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("mods", "max_compatible_mc_version")
