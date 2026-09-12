"""add_phase17_newsletter_and_digest_tables

Revision ID: e7f8a9b0c1d2
Revises: d6f7a8b9c0d1
Create Date: 2026-09-11 04:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Subscribers table
    op.create_table(
        'subscribers',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('age_group', sa.String(50), nullable=True),
        sa.Column('frequency', sa.String(50), nullable=False, server_default='DAILY'),
        sa.Column('preferred_channel', sa.String(50), nullable=False, server_default='EMAIL'),
        sa.Column('topics', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('unsubscribe_token', sa.String(100), nullable=False),
        sa.Column('unsubscribed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f('ix_subscribers_email'), 'subscribers', ['email'], unique=True)
    op.create_index(op.f('ix_subscribers_unsubscribe_token'), 'subscribers', ['unsubscribe_token'], unique=True)
    op.create_index(op.f('ix_subscribers_is_active'), 'subscribers', ['is_active'], unique=False)

    # 2. Digests table
    op.create_table(
        'digests',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('digest_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='DRAFT'),
        sa.Column('story_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('html_content', sa.Text(), nullable=False),
        sa.Column('plain_text_content', sa.Text(), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f('ix_digests_digest_date'), 'digests', ['digest_date'], unique=True)
    op.create_index(op.f('ix_digests_status'), 'digests', ['status'], unique=False)

    # 3. Digest Stories junction table
    op.create_table(
        'digest_stories',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('digest_id', sa.UUID(as_uuid=True), sa.ForeignKey('digests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('story_id', sa.UUID(as_uuid=True), sa.ForeignKey('stories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('ranking_score_snapshot', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('headline_override', sa.String(500), nullable=True),
        sa.Column('summary_override', sa.Text(), nullable=True),
        sa.Column('why_it_matters_override', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f('ix_digest_stories_digest_id'), 'digest_stories', ['digest_id'], unique=False)
    op.create_index(op.f('ix_digest_stories_story_id'), 'digest_stories', ['story_id'], unique=False)

    # 4. Delivery Records table
    op.create_table(
        'delivery_records',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('digest_id', sa.UUID(as_uuid=True), sa.ForeignKey('digests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('subscriber_id', sa.UUID(as_uuid=True), sa.ForeignKey('subscribers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(50), nullable=False, server_default='EMAIL'),
        sa.Column('status', sa.String(50), nullable=False, server_default='PENDING'),
        sa.Column('recipient_email', sa.String(255), nullable=False),
        sa.Column('provider', sa.String(50), nullable=False, server_default='RESEND'),
        sa.Column('provider_message_id', sa.String(255), nullable=True),
        sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('digest_id', 'subscriber_id', name='uq_delivery_records_digest_subscriber'),
    )
    op.create_index(op.f('ix_delivery_records_digest_id'), 'delivery_records', ['digest_id'], unique=False)
    op.create_index(op.f('ix_delivery_records_subscriber_id'), 'delivery_records', ['subscriber_id'], unique=False)
    op.create_index(op.f('ix_delivery_records_status'), 'delivery_records', ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('delivery_records')
    op.drop_table('digest_stories')
    op.drop_table('digests')
    op.drop_table('subscribers')
