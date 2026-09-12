"""update_stories_and_add_story_content_items

Revision ID: c5e6f7a8b9c0
Revises: b4de7a8912c3
Create Date: 2026-09-10 03:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c5e6f7a8b9c0'
down_revision: Union[str, None] = 'b4de7a8912c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update stories table
    op.add_column('stories', sa.Column('status', sa.String(length=50), nullable=False, server_default='ACTIVE'))
    op.add_column('stories', sa.Column('canonical_content_item_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('content_items.id', ondelete='SET NULL'), nullable=True))
    op.add_column('stories', sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'))
    op.create_index(op.f('ix_stories_status'), 'stories', ['status'], unique=False)

    # 2. Create story_content_items association table
    op.create_table(
        'story_content_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('story_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('stories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('content_item_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('content_items.id', ondelete='CASCADE'), nullable=False),
        sa.Column('is_canonical', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('confidence_score', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint('story_id', 'content_item_id', name='uq_story_content_item'),
    )

    op.create_index(
        op.f('ix_story_content_items_story_id'),
        'story_content_items',
        ['story_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_story_content_items_content_item_id'),
        'story_content_items',
        ['content_item_id'],
        unique=False,
    )
    op.create_index(
        'ix_story_content_items_story_canonical',
        'story_content_items',
        ['story_id', 'is_canonical'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_story_content_items_story_canonical', table_name='story_content_items')
    op.drop_index(op.f('ix_story_content_items_content_item_id'), table_name='story_content_items')
    op.drop_index(op.f('ix_story_content_items_story_id'), table_name='story_content_items')
    op.drop_table('story_content_items')

    op.drop_index(op.f('ix_stories_status'), table_name='stories')
    op.drop_column('stories', 'metadata_json')
    op.drop_column('stories', 'canonical_content_item_id')
    op.drop_column('stories', 'status')
