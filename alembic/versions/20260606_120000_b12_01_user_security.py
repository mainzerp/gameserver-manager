"""Add user security columns

Revision ID: b12_01_user_security
Revises: b11_04_server_gmod_gslt
Create Date: 2026-06-06 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b12_01_user_security"
down_revision: Union[str, None] = "b11_04_server_gmod_gslt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "failed_login_count",
                    sa.Integer(),
                    server_default="0",
                    nullable=False,
                )
            )
            batch_op.add_column(
                sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
            )
            batch_op.add_column(
                sa.Column(
                    "last_totp_used_at", sa.DateTime(timezone=True), nullable=True
                )
            )
    else:
        op.add_column(
            "users",
            sa.Column(
                "failed_login_count", sa.Integer(), server_default="0", nullable=False
            ),
        )
        op.add_column(
            "users",
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            "users",
            sa.Column("last_totp_used_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("last_totp_used_at")
            batch_op.drop_column("locked_until")
            batch_op.drop_column("failed_login_count")
    else:
        op.drop_column("users", "last_totp_used_at")
        op.drop_column("users", "locked_until")
        op.drop_column("users", "failed_login_count")
