"""batch2 server management: uptime schedule, tags, started_at

Revision ID: b9_02_batch2_server_mgmt
Revises: b9_01_batch1_quick_wins
Create Date: 2026-04-01 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9_02_batch2_server_mgmt"
down_revision: str = "b9_01_batch1_quick_wins"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A5: Uptime Schedule
    op.add_column("servers", sa.Column("uptime_schedule", sa.Text(), nullable=True))
    # For Batch 3 features (C1 tags, C3 started_at)
    op.add_column("servers", sa.Column("tags", sa.Text(), nullable=True))
    op.add_column(
        "servers", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("servers", "started_at")
    op.drop_column("servers", "tags")
    op.drop_column("servers", "uptime_schedule")
