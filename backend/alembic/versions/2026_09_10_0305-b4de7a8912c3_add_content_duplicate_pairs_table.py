"""add_content_duplicate_pairs_table

Revision ID: b4de7a8912c3
Revises: f3aa829c7102
Create Date: 2026-09-10 03:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b4de7a8912c3'
down_revision: Union[str, None] = 'f3aa829c7102'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'content_duplicate_pairs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('content_item_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('content_items.id', ondelete='CASCADE'), nullable=False),
        sa.Column('duplicate_content_item_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('content_items.id', ondelete='CASCADE'), nullable=False),
        sa.Column('similarity_score', sa.Float(), nullable=False),
        sa.Column('cosine_distance', sa.Float(), nullable=True),
        sa.Column('classification', sa.String(length=50), nullable=False),
        sa.Column('detection_method', sa.String(length=50), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint('content_item_id', 'duplicate_content_item_id', name='uq_content_duplicate_pair'),
    )

    op.create_index(
        op.f('ix_content_duplicate_pairs_content_item_id'),
        'content_duplicate_pairs',
        ['content_item_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_content_duplicate_pairs_duplicate_content_item_id'),
        'content_duplicate_pairs',
        ['duplicate_content_item_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_content_duplicate_pairs_similarity_score'),
        'content_duplicate_pairs',
        ['similarity_score'],
        unique=False,
    )
    op.create_index(
        op.f('ix_content_duplicate_pairs_classification'),
        'content_duplicate_pairs',
        ['classification'],
        unique=False,
    )
    op.create_index(
        op.f('ix_content_duplicate_pairs_detection_method'),
        'content_duplicate_pairs',
        ['detection_method'],
        unique=False,
    )
    op.create_index(
        'ix_content_duplicate_pairs_item_sim',
        'content_duplicate_pairs',
        ['content_item_id', 'similarity_score'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_content_duplicate_pairs_item_sim', table_name='content_duplicate_pairs')
    op.drop_index(op.f('ix_content_duplicate_pairs_detection_method'), table_name='content_duplicate_pairs')
    op.drop_index(op.f('ix_content_duplicate_pairs_classification'), table_name='content_duplicate_pairs')
    op.drop_index(op.f('ix_content_duplicate_pairs_similarity_score'), table_name='content_duplicate_pairs')
    op.drop_index(op.f('ix_content_duplicate_pairs_duplicate_content_item_id'), table_name='content_duplicate_pairs')
    op.drop_index(op.f('ix_content_duplicate_pairs_content_item_id'), table_name='content_duplicate_pairs')
    op.drop_table('content_duplicate_pairs')
