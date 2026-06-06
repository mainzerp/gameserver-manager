"""add crash auto-restart fields to servers

Revision ID: b8_01_crash_restart
Revises: b7_01_curseforge_key
Create Date: 2026-04-01 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8_01_crash_restart"
down_revision: str = "b7_01_curseforge_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column(
            "auto_restart_on_crash", sa.Boolean(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "max_crash_restarts", sa.Integer(), nullable=False, server_default="3"
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "crash_restart_delay", sa.Integer(), nullable=False, server_default="15"
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "crash_stability_window", sa.Integer(), nullable=False, server_default="600"
        ),
    )


def downgrade() -> None:
    op.drop_column("servers", "crash_stability_window")
    op.drop_column("servers", "crash_restart_delay")
    op.drop_column("servers", "max_crash_restarts")
    op.drop_column("servers", "auto_restart_on_crash")
