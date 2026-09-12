"""add_story_ranking_and_curation_fields

Revision ID: d6f7a8b9c0d1
Revises: c5e6f7a8b9c0
Create Date: 2026-09-10 03:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6f7a8b9c0d1'
down_revision: Union[str, None] = 'c5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'stories',
        sa.Column('ranking_score', sa.Float(), nullable=False, server_default='0.0'),
    )
    op.add_column(
        'stories',
        sa.Column('ranking_updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'stories',
        sa.Column('ranking_metadata', sa.JSON(), nullable=False, server_default='{}'),
    )
    op.add_column(
        'stories',
        sa.Column('curated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'stories',
        sa.Column('curation_metadata', sa.JSON(), nullable=False, server_default='{}'),
    )

    op.create_index(
        op.f('ix_stories_ranking_score'),
        'stories',
        ['ranking_score'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_stories_ranking_score'), table_name='stories')
    op.drop_column('stories', 'curation_metadata')
    op.drop_column('stories', 'curated_at')
    op.drop_column('stories', 'ranking_metadata')
    op.drop_column('stories', 'ranking_updated_at')
    op.drop_column('stories', 'ranking_score')
