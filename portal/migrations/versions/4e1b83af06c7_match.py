"""сверка с чертежами: строки и итог прогона

Revision ID: 4e1b83af06c7
Revises: 3c9e40b7d512
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '4e1b83af06c7'
down_revision = '3c9e40b7d512'
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.add_column('document', sa.Column('match_stats', JSONB, nullable=False,
                                        server_default='{}'))
    op.add_column('document', sa.Column('matched_at', sa.DateTime(timezone=True),
                                        nullable=True))
    op.create_table(
        'match_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('mark', sa.String(length=300), nullable=False),
        sa.Column('marks', JSONB, nullable=False),
        sa.Column('names', sa.Text(), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False),
        sa.Column('spec_qty', sa.Float(), nullable=True),
        sa.Column('plan_qty', sa.Float(), nullable=True),
        sa.Column('plan_raw', sa.Float(), nullable=True),
        sa.Column('schema_qty', sa.Float(), nullable=True),
        sa.Column('schema_raw', sa.Float(), nullable=True),
        sa.Column('exact_qty', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=80), nullable=False),
        sa.Column('level', sa.String(length=10), nullable=False),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('keys', JSONB, nullable=False),
        sa.Column('in_plan', sa.Boolean(), nullable=False),
        sa.Column('spec_pages', JSONB, nullable=False),
        sa.Column('plan_pages', JSONB, nullable=False),
        sa.Column('schema_pages', JSONB, nullable=False),
        sa.Column('sections', JSONB, nullable=False),
        sa.Column('verdict', sa.String(length=10), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_match_item_document_id', 'match_item', ['document_id'])
    op.create_index('ix_match_item_level', 'match_item', ['level'])
    op.create_index('ix_match_item_in_plan', 'match_item', ['in_plan'])
    op.create_index('ix_match_item_doc_level', 'match_item',
                    ['document_id', 'level'])


def downgrade():
    op.drop_table('match_item')
    op.drop_column('document', 'matched_at')
    op.drop_column('document', 'match_stats')
