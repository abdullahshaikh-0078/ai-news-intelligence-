"""add_language_and_constraints_to_sources

Revision ID: dfe6a65ca915
Revises: 9d54944eede9
Create Date: 2026-09-07 11:29:15.524401

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dfe6a65ca915'
down_revision: Union[str, None] = '9d54944eede9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sources', sa.Column('language', sa.String(length=10), server_default='en', nullable=False))
    op.create_check_constraint('ck_source_reliability_score', 'sources', 'reliability_score >= 0.0 AND reliability_score <= 1.0')
    op.create_check_constraint('ck_source_fetch_interval_minutes', 'sources', 'fetch_interval_minutes >= 5')


def downgrade() -> None:
    op.drop_constraint('ck_source_fetch_interval_minutes', 'sources', type_='check')
    op.drop_constraint('ck_source_reliability_score', 'sources', type_='check')
    op.drop_column('sources', 'language')
