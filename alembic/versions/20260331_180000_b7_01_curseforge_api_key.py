"""drop curseforge_api_key_enc from site_settings

Revision ID: b7_01_curseforge_key
Revises: a9f1c2d3e4f5
Create Date: 2026-03-31 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7_01_curseforge_key"
down_revision: str = "a9f1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL with IF EXISTS to handle fresh databases where the column was never added
    op.execute("ALTER TABLE site_settings DROP COLUMN IF EXISTS curseforge_api_key_enc")


def downgrade() -> None:
    op.add_column(
        "site_settings", sa.Column("curseforge_api_key_enc", sa.Text(), nullable=True)
    )
