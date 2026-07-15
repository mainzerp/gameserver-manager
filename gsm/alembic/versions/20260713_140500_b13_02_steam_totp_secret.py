"""Add steam_guard_secret_encrypted to steam_accounts

Revision ID: b13_02_steam_totp_secret
Revises: b13_01_query_port
Create Date: 2026-07-13 14:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b13_02_steam_totp_secret"
down_revision: Union[str, None] = "b13_01_query_port"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "steam_accounts",
        sa.Column("steam_guard_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("steam_accounts", "steam_guard_secret_encrypted")
