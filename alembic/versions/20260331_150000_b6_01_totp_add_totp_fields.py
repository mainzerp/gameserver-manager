"""add_totp_fields

Revision ID: b6_01_totp
Revises: a1c577628359
Create Date: 2026-03-31 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6_01_totp"
down_revision: Union[str, None] = "a1c577628359"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("totp_secret", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("totp_enabled", sa.Boolean(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("recovery_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("recovery_codes")
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret")
