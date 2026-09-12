"""add_content_type_and_thumbnail_to_content_items

Revision ID: e2cca71edb01
Revises: d1bbec0dcfae
Create Date: 2026-09-09 23:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2cca71edb01'
down_revision: Union[str, None] = 'd1bbec0dcfae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add canonical columns
    op.add_column(
        'content_items',
        sa.Column('content_type', sa.String(length=50), server_default='ARTICLE', nullable=False),
    )
    op.add_column(
        'content_items',
        sa.Column('thumbnail_url', sa.String(length=2048), nullable=True),
    )
    op.create_index(
        op.f('ix_content_items_content_type'),
        'content_items',
        ['content_type'],
        unique=False,
    )

    # 2. Backfill existing items based on their origin source type and metadata
    op.execute("""
        UPDATE content_items ci
        SET content_type = 'RESEARCH_PAPER'
        FROM sources s
        WHERE ci.source_id = s.id AND (s.type = 'ARXIV' OR s.slug = 'arxiv-ai');
    """)
    op.execute("""
        UPDATE content_items ci
        SET content_type = 'COMMUNITY_POST'
        FROM sources s
        WHERE ci.source_id = s.id AND (s.slug = 'hacker-news' OR (s.config->>'adapter_type') = 'hacker_news');
    """)
    op.execute("""
        UPDATE content_items ci
        SET content_type = 'VIDEO'
        FROM sources s
        WHERE ci.source_id = s.id AND s.type = 'YOUTUBE';
    """)
    op.execute("""
        UPDATE content_items
        SET thumbnail_url = metadata_json->>'thumbnail_url'
        WHERE thumbnail_url IS NULL AND metadata_json->>'thumbnail_url' IS NOT NULL;
    """)


def downgrade() -> None:
    op.drop_index(op.f('ix_content_items_content_type'), table_name='content_items')
    op.drop_column('content_items', 'thumbnail_url')
    op.drop_column('content_items', 'content_type')
