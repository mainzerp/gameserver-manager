"""add_rbac

Revision ID: b6_02_rbac
Revises: b6_01_totp
Create Date: 2026-03-31 15:01:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6_02_rbac"
down_revision: Union[str, None] = "b6_01_totp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "role", sa.String(length=20), server_default="admin", nullable=False
            )
        )

    # Set existing admin users to 'admin' role (dialect-agnostic)
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("UPDATE users SET role = 'admin' WHERE is_admin = 1")
        op.execute("UPDATE users SET role = 'viewer' WHERE is_admin = 0")
    else:
        op.execute("UPDATE users SET role = 'admin' WHERE is_admin = true")
        op.execute("UPDATE users SET role = 'viewer' WHERE is_admin = false")

    op.create_table(
        "server_access",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "server_id"),
    )
    with op.batch_alter_table("server_access", schema=None) as batch_op:
        batch_op.create_index("ix_server_access_user_id", ["user_id"])
        batch_op.create_index("ix_server_access_server_id", ["server_id"])


def downgrade() -> None:
    op.drop_table("server_access")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("role")
