"""Add FK ondelete cascades, missing indexes, and fix user role default

Revision ID: b12_02_fk_cascades_indexes
Revises: b11_04_server_gmod_gslt
Create Date: 2026-06-06 12:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b12_02_fk_cascades_indexes"
down_revision: Union[str, None] = "b11_04_server_gmod_gslt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _pg_drop_fk(table: str, column: str):
    """Drop an unnamed FK constraint on PostgreSQL by looking it up in information_schema."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = :table AND constraint_type = 'FOREIGN KEY'
            AND constraint_name IN (
                SELECT constraint_name FROM information_schema.constraint_column_usage
                WHERE table_name = :ref_table AND column_name = :ref_col
            )
            """
        ),
        {"table": table, "ref_table": "servers", "ref_col": "id"},
    )
    rows = result.fetchall()
    for row in rows:
        op.drop_constraint(row[0], table, type_="foreignkey")


def _pg_recreate_fk(
    table: str,
    columns: list[str],
    ref_table: str,
    ref_columns: list[str],
    ondelete: str,
):
    """Recreate a FK constraint with ondelete on PostgreSQL."""
    op.create_foreign_key(
        None, table, ref_table, columns, ref_columns, ondelete=ondelete
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ── Indexes (safe on all dialects) ──────────────────────────
    op.create_index(op.f("ix_servers_status"), "servers", ["status"], unique=False)
    op.create_index(
        op.f("ix_servers_server_type"), "servers", ["server_type"], unique=False
    )
    op.create_index(op.f("ix_servers_node_id"), "servers", ["node_id"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)
    op.create_index(
        op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False
    )

    # ── Fix user.role default ───────────────────────────────────
    if dialect == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.alter_column(
                "role",
                existing_type=sa.String(length=20),
                existing_server_default=sa.text("'admin'"),
                server_default=sa.text("'viewer'"),
            )
    else:
        op.alter_column(
            "users",
            "role",
            existing_type=sa.String(length=20),
            existing_server_default=sa.text("'admin'"),
            server_default=sa.text("'viewer'"),
        )

    # ── Foreign Key ondelete changes ────────────────────────────
    # SQLite does not enforce FKs by default (PRAGMA foreign_keys=OFF).
    # Recreating tables with correct constraints is extremely complex,
    # so we skip constraint migration for SQLite. New SQLite DBs will
    # get the correct constraints from the model definitions.
    # PostgreSQL gets the constraints updated via drop/create.
    if dialect == "postgresql":
        # servers.steam_account_id -> SET NULL
        op.drop_constraint(
            "servers_steam_account_id_fkey", "servers", type_="foreignkey"
        )
        op.create_foreign_key(
            None,
            "servers",
            "steam_accounts",
            ["steam_account_id"],
            ["id"],
            ondelete="SET NULL",
        )

        # servers.node_id -> SET NULL
        op.drop_constraint("servers_node_id_fkey", "servers", type_="foreignkey")
        op.create_foreign_key(
            None,
            "servers",
            "nodes",
            ["node_id"],
            ["id"],
            ondelete="SET NULL",
        )

        # backups.server_id -> CASCADE
        op.drop_constraint("backups_server_id_fkey", "backups", type_="foreignkey")
        op.create_foreign_key(
            None,
            "backups",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # mods.server_id -> CASCADE
        op.drop_constraint("mods_server_id_fkey", "mods", type_="foreignkey")
        op.create_foreign_key(
            None, "mods", "servers", ["server_id"], ["id"], ondelete="CASCADE"
        )

        # scheduled_tasks.server_id -> CASCADE
        op.drop_constraint(
            "scheduled_tasks_server_id_fkey", "scheduled_tasks", type_="foreignkey"
        )
        op.create_foreign_key(
            None,
            "scheduled_tasks",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # metric_snapshots.server_id -> CASCADE
        op.drop_constraint(
            "metric_snapshots_server_id_fkey", "metric_snapshots", type_="foreignkey"
        )
        op.create_foreign_key(
            None,
            "metric_snapshots",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # workshop_items.server_id -> CASCADE
        op.drop_constraint(
            "workshop_items_server_id_fkey", "workshop_items", type_="foreignkey"
        )
        op.create_foreign_key(
            None,
            "workshop_items",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # invite_links.server_id -> CASCADE
        op.drop_constraint(
            "invite_links_server_id_fkey", "invite_links", type_="foreignkey"
        )
        op.create_foreign_key(
            None,
            "invite_links",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # api_keys.user_id -> CASCADE
        op.drop_constraint("api_keys_user_id_fkey", "api_keys", type_="foreignkey")
        op.create_foreign_key(
            None, "api_keys", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Indexes
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_servers_node_id"), table_name="servers")
    op.drop_index(op.f("ix_servers_server_type"), table_name="servers")
    op.drop_index(op.f("ix_servers_status"), table_name="servers")

    # user.role default
    if dialect == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.alter_column(
                "role",
                existing_type=sa.String(length=20),
                existing_server_default=sa.text("'viewer'"),
                server_default=sa.text("'admin'"),
            )
    else:
        op.alter_column(
            "users",
            "role",
            existing_type=sa.String(length=20),
            existing_server_default=sa.text("'viewer'"),
            server_default=sa.text("'admin'"),
        )

    # FK ondelete (PostgreSQL only)
    if dialect == "postgresql":
        op.drop_constraint(
            "servers_steam_account_id_fkey", "servers", type_="foreignkey"
        )
        op.create_foreign_key(
            None, "servers", "steam_accounts", ["steam_account_id"], ["id"]
        )

        op.drop_constraint("servers_node_id_fkey", "servers", type_="foreignkey")
        op.create_foreign_key(None, "servers", "nodes", ["node_id"], ["id"])

        op.drop_constraint("backups_server_id_fkey", "backups", type_="foreignkey")
        op.create_foreign_key(None, "backups", "servers", ["server_id"], ["id"])

        op.drop_constraint("mods_server_id_fkey", "mods", type_="foreignkey")
        op.create_foreign_key(None, "mods", "servers", ["server_id"], ["id"])

        op.drop_constraint(
            "scheduled_tasks_server_id_fkey", "scheduled_tasks", type_="foreignkey"
        )
        op.create_foreign_key(None, "scheduled_tasks", "servers", ["server_id"], ["id"])

        op.drop_constraint(
            "metric_snapshots_server_id_fkey", "metric_snapshots", type_="foreignkey"
        )
        op.create_foreign_key(
            None, "metric_snapshots", "servers", ["server_id"], ["id"]
        )

        op.drop_constraint(
            "workshop_items_server_id_fkey", "workshop_items", type_="foreignkey"
        )
        op.create_foreign_key(None, "workshop_items", "servers", ["server_id"], ["id"])

        op.drop_constraint(
            "invite_links_server_id_fkey", "invite_links", type_="foreignkey"
        )
        op.create_foreign_key(None, "invite_links", "servers", ["server_id"], ["id"])

        op.drop_constraint("api_keys_user_id_fkey", "api_keys", type_="foreignkey")
        op.create_foreign_key(None, "api_keys", "users", ["user_id"], ["id"])
