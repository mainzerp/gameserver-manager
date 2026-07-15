"""Widen workshop item size to bigint

Revision ID: b11_03_workshop_file_size_bigint
Revises: b11_02_backup_size_bigint
Create Date: 2026-04-19 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b11_03_workshop_file_size_bigint"
down_revision: Union[str, None] = "b11_02_backup_size_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("workshop_items") as batch_op:
            batch_op.alter_column(
                "file_size",
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "workshop_items",
            "file_size",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("workshop_items") as batch_op:
            batch_op.alter_column(
                "file_size",
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "workshop_items",
            "file_size",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
