"""SteamCMD full integration

Revision ID: b11_01_steamcmd_integration
Revises: b10_02_mod_max_compat
Create Date: 2026-04-04 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b11_01_steamcmd_integration"
down_revision: Union[str, None] = "b10_02_mod_max_compat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create steam_accounts table
    op.create_table(
        "steam_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "steam_guard_type", sa.String(20), server_default="none", nullable=True
        ),
        sa.Column("is_anonymous", sa.Boolean(), server_default="0", nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create workshop_items table
    op.create_table(
        "workshop_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("workshop_id", sa.String(20), nullable=False),
        sa.Column("app_id", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("installed", sa.Boolean(), server_default="0", nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add Steam columns to servers table
    op.add_column("servers", sa.Column("steam_build_id", sa.String(50), nullable=True))
    op.add_column(
        "servers",
        sa.Column(
            "steam_branch", sa.String(100), server_default="public", nullable=True
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "steam_login_anonymous", sa.Boolean(), server_default="1", nullable=True
        ),
    )
    op.add_column("servers", sa.Column("steam_account_id", sa.Integer(), nullable=True))
    op.add_column(
        "servers",
        sa.Column(
            "steam_update_on_start", sa.Boolean(), server_default="0", nullable=True
        ),
    )
    op.add_column(
        "servers",
        sa.Column("steam_last_update", sa.DateTime(timezone=True), nullable=True),
    )

    # Add FK for steam_account_id (skip on SQLite as it doesn't support ADD CONSTRAINT well)
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_servers_steam_account_id",
            "servers",
            "steam_accounts",
            ["steam_account_id"],
            ["id"],
        )

    # Note: STEAM_UPDATE and STEAM_VALIDATE enum values for TaskType
    # SQLite stores enums as strings, so no DDL needed.
    # For PostgreSQL, add values to the enum type if it exists:
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'steam_update'")
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'steam_validate'")

    # Add steam_api_key to site_settings
    op.add_column("site_settings", sa.Column("steam_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.drop_constraint("fk_servers_steam_account_id", "servers", type_="foreignkey")

    op.drop_column("site_settings", "steam_api_key")
    op.drop_column("servers", "steam_last_update")
    op.drop_column("servers", "steam_update_on_start")
    op.drop_column("servers", "steam_account_id")
    op.drop_column("servers", "steam_login_anonymous")
    op.drop_column("servers", "steam_branch")
    op.drop_column("servers", "steam_build_id")

    op.drop_table("workshop_items")
    op.drop_table("steam_accounts")

    # Note: PostgreSQL enum value removal is not supported via ALTER TYPE;
    # the values will remain but be unused after downgrade.
