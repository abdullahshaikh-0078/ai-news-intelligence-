"""add_ai_processing_fields_and_embedding

Revision ID: f3aa829c7102
Revises: e2cca71edb01
Create Date: 2026-09-09 23:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3aa829c7102'
down_revision: Union[str, None] = 'e2cca71edb01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Ensure vector type exists (either via native extension or fallback domain)
    # and add embedding column
    op.execute("""
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS vector;
            EXCEPTION WHEN OTHERS THEN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector') THEN
                    CREATE DOMAIN vector AS double precision[];
                END IF;
            END;

            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                EXECUTE 'ALTER TABLE content_items ADD COLUMN IF NOT EXISTS embedding vector(1536);';
            ELSIF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector') THEN
                EXECUTE 'ALTER TABLE content_items ADD COLUMN IF NOT EXISTS embedding vector;';
            ELSE
                EXECUTE 'ALTER TABLE content_items ADD COLUMN IF NOT EXISTS embedding double precision[];';
            END IF;
        END $$;
    """)

    # 2. Add structured AI intelligence columns
    op.add_column('content_items', sa.Column('ai_summary', sa.Text(), nullable=True))
    op.add_column('content_items', sa.Column('ai_key_points', sa.JSON(), nullable=True))
    op.add_column('content_items', sa.Column('ai_topics', sa.JSON(), nullable=True))
    op.add_column('content_items', sa.Column('ai_relevance_score', sa.Float(), nullable=True))
    op.add_column('content_items', sa.Column('ai_model', sa.String(length=100), nullable=True))
    op.add_column('content_items', sa.Column('ai_processed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('content_items', sa.Column('embedding_model', sa.String(length=100), nullable=True))

    # 4. Create index on ai_relevance_score for efficient ranking & filtering
    op.create_index(
        op.f('ix_content_items_ai_relevance_score'),
        'content_items',
        ['ai_relevance_score'],
        unique=False,
    )

    # 5. Migrate existing RAW states to PENDING for unified lifecycle tracking
    op.execute("""
        UPDATE content_items
        SET processing_state = 'PENDING'
        WHERE processing_state = 'RAW';
    """)


def downgrade() -> None:
    # 1. Drop index
    op.drop_index(op.f('ix_content_items_ai_relevance_score'), table_name='content_items')

    # 2. Drop AI and embedding columns
    op.execute("ALTER TABLE content_items DROP COLUMN IF EXISTS embedding;")
    op.drop_column('content_items', 'embedding_model')
    op.drop_column('content_items', 'ai_processed_at')
    op.drop_column('content_items', 'ai_model')
    op.drop_column('content_items', 'ai_relevance_score')
    op.drop_column('content_items', 'ai_topics')
    op.drop_column('content_items', 'ai_key_points')
    op.drop_column('content_items', 'ai_summary')

    # 3. Revert PENDING to RAW
    op.execute("""
        UPDATE content_items
        SET processing_state = 'RAW'
        WHERE processing_state = 'PENDING';
    """)
