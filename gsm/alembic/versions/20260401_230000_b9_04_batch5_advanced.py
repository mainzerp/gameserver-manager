"""batch5: modpack import, java auto-download, mod management improvements

Revision ID: b9_04_batch5_advanced
Revises: b9_03_batch4_backup_status
Create Date: 2026-04-01 23:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9_04_batch5_advanced"
down_revision: str = "b9_03_batch4_backup_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Mod profiles table
    op.create_table(
        "mod_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("server_type", sa.String(50), nullable=False),
        sa.Column("loader", sa.String(50), nullable=True),
        sa.Column("mc_version", sa.String(20), nullable=True),
        sa.Column("mods_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("mod_profiles")
