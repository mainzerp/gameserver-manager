"""batch6: invite links, saved commands

Revision ID: b9_05_batch6_extras
Revises: b9_04_batch5_advanced
Create Date: 2026-04-01 24:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9_05_batch6_extras"
down_revision: str = "b9_04_batch5_advanced"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Invite links table
    op.create_table(
        "invite_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "server_id", sa.Integer(), sa.ForeignKey("servers.id"), nullable=True
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    # Saved commands column on servers
    op.add_column("servers", sa.Column("saved_commands", sa.Text(), nullable=True))

    # Backup external path on site_settings
    op.add_column(
        "site_settings", sa.Column("backup_external_path", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("site_settings", "backup_external_path")
    op.drop_column("servers", "saved_commands")
    op.drop_table("invite_links")
