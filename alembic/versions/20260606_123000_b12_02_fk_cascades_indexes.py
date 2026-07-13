"""Add FK ondelete cascades, missing indexes, and fix user role default

Revision ID: b12_02_fk_cascades_indexes
Revises: b12_01_user_security
Create Date: 2026-06-06 12:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b12_02_fk_cascades_indexes"
down_revision: Union[str, None] = "b12_01_user_security"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _pg_drop_fk(table: str, column: str):
    """Drop the FK constraint on a table.column by looking up its actual name.

    Different schema generations name constraints differently (e.g.
    ``fk_servers_steam_account_id`` vs ``servers_steam_account_id_fkey``),
    so we resolve the name from information_schema instead of guessing.
    """
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = :table
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = :column
            """
        ),
        {"table": table, "column": column},
    )
    for row in result.fetchall():
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
        _pg_drop_fk("servers", "steam_account_id")
        op.create_foreign_key(
            None,
            "servers",
            "steam_accounts",
            ["steam_account_id"],
            ["id"],
            ondelete="SET NULL",
        )

        # servers.node_id -> SET NULL
        _pg_drop_fk("servers", "node_id")
        op.create_foreign_key(
            None,
            "servers",
            "nodes",
            ["node_id"],
            ["id"],
            ondelete="SET NULL",
        )

        # backups.server_id -> CASCADE
        _pg_drop_fk("backups", "server_id")
        op.create_foreign_key(
            None,
            "backups",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # mods.server_id -> CASCADE
        _pg_drop_fk("mods", "server_id")
        op.create_foreign_key(
            None, "mods", "servers", ["server_id"], ["id"], ondelete="CASCADE"
        )

        # scheduled_tasks.server_id -> CASCADE
        _pg_drop_fk("scheduled_tasks", "server_id")
        op.create_foreign_key(
            None,
            "scheduled_tasks",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # metric_snapshots.server_id -> CASCADE
        _pg_drop_fk("metric_snapshots", "server_id")
        op.create_foreign_key(
            None,
            "metric_snapshots",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # workshop_items.server_id -> CASCADE
        _pg_drop_fk("workshop_items", "server_id")
        op.create_foreign_key(
            None,
            "workshop_items",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # invite_links.server_id -> CASCADE
        _pg_drop_fk("invite_links", "server_id")
        op.create_foreign_key(
            None,
            "invite_links",
            "servers",
            ["server_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # api_keys.user_id -> CASCADE
        _pg_drop_fk("api_keys", "user_id")
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
        _pg_drop_fk("servers", "steam_account_id")
        op.create_foreign_key(
            None, "servers", "steam_accounts", ["steam_account_id"], ["id"]
        )

        _pg_drop_fk("servers", "node_id")
        op.create_foreign_key(None, "servers", "nodes", ["node_id"], ["id"])

        _pg_drop_fk("backups", "server_id")
        op.create_foreign_key(None, "backups", "servers", ["server_id"], ["id"])

        _pg_drop_fk("mods", "server_id")
        op.create_foreign_key(None, "mods", "servers", ["server_id"], ["id"])

        _pg_drop_fk("scheduled_tasks", "server_id")
        op.create_foreign_key(None, "scheduled_tasks", "servers", ["server_id"], ["id"])

        _pg_drop_fk("metric_snapshots", "server_id")
        op.create_foreign_key(
            None, "metric_snapshots", "servers", ["server_id"], ["id"]
        )

        _pg_drop_fk("workshop_items", "server_id")
        op.create_foreign_key(None, "workshop_items", "servers", ["server_id"], ["id"])

        _pg_drop_fk("invite_links", "server_id")
        op.create_foreign_key(None, "invite_links", "servers", ["server_id"], ["id"])

        _pg_drop_fk("api_keys", "user_id")
        op.create_foreign_key(None, "api_keys", "users", ["user_id"], ["id"])
