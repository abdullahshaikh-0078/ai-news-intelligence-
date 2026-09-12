"""add_provider_message_id_index

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        op.f('ix_delivery_records_provider_message_id'),
        'delivery_records',
        ['provider_message_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_delivery_records_provider_message_id'),
        table_name='delivery_records',
    )
