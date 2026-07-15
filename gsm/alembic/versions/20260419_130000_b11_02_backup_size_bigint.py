"""Promote backup size_bytes to bigint.

Revision ID: b11_02_backup_size_bigint
Revises: b11_01_steamcmd_integration
Create Date: 2026-04-19 13:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b11_02_backup_size_bigint"
down_revision = "b11_01_steamcmd_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "backups",
        "size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "backups",
        "size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
