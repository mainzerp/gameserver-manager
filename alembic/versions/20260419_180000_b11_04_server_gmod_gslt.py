"""Add encrypted per-server GMod GSLT storage

Revision ID: b11_04_server_gmod_gslt
Revises: b11_03_workshop_file_size_bigint
Create Date: 2026-04-19 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b11_04_server_gmod_gslt"
down_revision: Union[str, None] = "b11_03_workshop_file_size_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("servers") as batch_op:
            batch_op.add_column(
                sa.Column("steam_gslt_encrypted", sa.Text(), nullable=True)
            )
    else:
        op.add_column(
            "servers", sa.Column("steam_gslt_encrypted", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("servers") as batch_op:
            batch_op.drop_column("steam_gslt_encrypted")
    else:
        op.drop_column("servers", "steam_gslt_encrypted")
