"""batch1 quick wins: jvm flags, readiness detection, scheduler improvements

Revision ID: b9_01_batch1_quick_wins
Revises: b8_01_crash_restart
Create Date: 2026-04-01 20:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9_01_batch1_quick_wins"
down_revision: str = "b8_01_crash_restart"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A3: JVM Flags / Startup Parameters
    op.add_column("servers", sa.Column("jvm_flags", sa.Text(), nullable=True))
    op.add_column("servers", sa.Column("server_args", sa.Text(), nullable=True))

    # A9: Startup Readiness Detection
    op.add_column(
        "servers", sa.Column("ready_log_pattern", sa.String(500), nullable=True)
    )

    # B4: Scheduler Improvements
    op.add_column(
        "scheduled_tasks", sa.Column("condition", sa.String(50), nullable=True)
    )
    op.add_column("scheduled_tasks", sa.Column("last_result", sa.Text(), nullable=True))
    op.add_column(
        "scheduled_tasks", sa.Column("last_duration_ms", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    # B4
    op.drop_column("scheduled_tasks", "last_duration_ms")
    op.drop_column("scheduled_tasks", "last_result")
    op.drop_column("scheduled_tasks", "condition")

    # A9
    op.drop_column("servers", "ready_log_pattern")

    # A3
    op.drop_column("servers", "server_args")
    op.drop_column("servers", "jvm_flags")
