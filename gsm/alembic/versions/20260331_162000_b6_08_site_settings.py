"""add_site_settings_table

Revision ID: b6_08_site_settings
Revises: b6_07_nodes
Create Date: 2026-03-31 16:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6_08_site_settings"
down_revision: Union[str, None] = "b6_07_nodes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        # SMTP
        sa.Column("smtp_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("smtp_host", sa.String(255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), server_default="587", nullable=False),
        sa.Column("smtp_user", sa.String(255), nullable=True),
        sa.Column("smtp_password_enc", sa.Text(), nullable=True),
        sa.Column("smtp_use_tls", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("smtp_from_address", sa.String(255), nullable=True),
        sa.Column("smtp_to_addresses", sa.Text(), nullable=True),
        sa.Column(
            "smtp_notify_events",
            sa.String(500),
            server_default="crash,backup_failed",
            nullable=False,
        ),
        # TOTP
        sa.Column(
            "totp_global_enabled", sa.Boolean(), server_default="false", nullable=False
        ),
        # Multi-node
        sa.Column(
            "multi_node_enabled", sa.Boolean(), server_default="false", nullable=False
        ),
        # WebAuthn
        sa.Column(
            "webauthn_enabled", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "webauthn_rp_id", sa.String(255), server_default="localhost", nullable=False
        ),
        sa.Column(
            "webauthn_origin",
            sa.String(500),
            server_default="https://localhost:8443",
            nullable=False,
        ),
        # Discord
        sa.Column("discord_webhook_url", sa.Text(), nullable=True),
        sa.Column(
            "discord_notify_events",
            sa.String(500),
            server_default="start,stop,crash,backup",
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seed the single settings row with safe defaults
    op.execute(
        "INSERT INTO site_settings (id, smtp_enabled, smtp_port, smtp_use_tls, "
        "smtp_notify_events, totp_global_enabled, multi_node_enabled, webauthn_enabled, "
        "webauthn_rp_id, webauthn_origin, discord_notify_events) "
        "VALUES (1, false, 587, true, 'crash,backup_failed', false, false, false, "
        "'localhost', 'https://localhost:8443', 'start,stop,crash,backup')"
    )


def downgrade() -> None:
    op.drop_table("site_settings")
