"""batch4: backup improvements, status page, notifications

Revision ID: b9_03_batch4_backup_status
Revises: b9_02_batch2_server_mgmt
Create Date: 2026-04-01 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9_03_batch4_backup_status"
down_revision: str = "b9_02_batch2_server_mgmt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # B1: Backup model improvements
    op.add_column(
        "backups",
        sa.Column("backup_type", sa.String(20), nullable=False, server_default="full"),
    )
    op.add_column(
        "backups",
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "backups",
        sa.Column("compressed", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column("backups", sa.Column("retention_days", sa.Integer(), nullable=True))

    # B1: Backup exclusion patterns on server
    op.add_column(
        "servers", sa.Column("backup_exclude_patterns", sa.Text(), nullable=True)
    )

    # C4: Per-server notification settings
    op.add_column(
        "servers", sa.Column("notification_webhook_url", sa.Text(), nullable=True)
    )
    op.add_column(
        "servers",
        sa.Column(
            "notifications_muted", sa.Boolean(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "servers", sa.Column("notification_events", sa.String(500), nullable=True)
    )

    # C4: Telegram integration in site_settings
    op.add_column(
        "site_settings", sa.Column("telegram_bot_token", sa.Text(), nullable=True)
    )
    op.add_column(
        "site_settings", sa.Column("telegram_chat_id", sa.String(255), nullable=True)
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "telegram_notify_events",
            sa.String(500),
            nullable=False,
            server_default="crash",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_settings", "telegram_notify_events")
    op.drop_column("site_settings", "telegram_chat_id")
    op.drop_column("site_settings", "telegram_bot_token")

    op.drop_column("servers", "notification_events")
    op.drop_column("servers", "notifications_muted")
    op.drop_column("servers", "notification_webhook_url")
    op.drop_column("servers", "backup_exclude_patterns")

    op.drop_column("backups", "retention_days")
    op.drop_column("backups", "compressed")
    op.drop_column("backups", "file_count")
    op.drop_column("backups", "backup_type")
