"""init

Revision ID: 111c88235453
Revises:
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '111c88235453'
down_revision = None
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.create_table(
        'org',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('plan', sa.String(length=40), nullable=False),
        sa.Column('limits', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'))
    op.create_table(
        'project',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('bureau', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_project_org_id'), 'project', ['org_id'])
    op.create_table(
        'submission',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=80), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_submission_project_id'), 'submission', ['project_id'])
    op.create_table(
        'document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=400), nullable=False),
        sa.Column('cipher', sa.String(length=120), nullable=False),
        sa.Column('section', sa.String(length=40), nullable=False),
        sa.Column('section_label', sa.String(length=120), nullable=False),
        sa.Column('revision', sa.String(length=80), nullable=False),
        sa.Column('file_key', sa.String(length=500), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('pages_total', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('capabilities', JSONB, nullable=False),
        sa.Column('kind_counts', JSONB, nullable=False),
        sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['submission_id'], ['submission.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_document_org_id'), 'document', ['org_id'])
    op.create_index(op.f('ix_document_status'), 'document', ['status'])
    op.create_index(op.f('ix_document_submission_id'), 'document', ['submission_id'])
    op.create_table(
        'run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('stage', sa.String(length=80), nullable=False),
        sa.Column('done', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('percent', sa.Integer(), nullable=False),
        sa.Column('stats', JSONB, nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_run_document_id'), 'run', ['document_id'])
    op.create_index(op.f('ix_run_org_id'), 'run', ['org_id'])
    op.create_table(
        'sheet',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('page', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('kind_override', sa.String(length=20), nullable=False),
        sa.Column('code', sa.String(length=120), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('mult', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_sheet_doc_page', 'sheet', ['document_id', 'page'])
    op.create_index(op.f('ix_sheet_document_id'), 'sheet', ['document_id'])
    op.create_table(
        'spec_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('page', sa.Integer(), nullable=False),
        sa.Column('pos', sa.String(length=40), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('mark', sa.String(length=300), nullable=False),
        sa.Column('canon_mark', sa.String(length=300), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False),
        sa.Column('qty', sa.Float(), nullable=True),
        sa.Column('qty_raw', sa.String(length=80), nullable=False),
        sa.Column('section', sa.String(length=80), nullable=False),
        sa.Column('category', sa.String(length=300), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('excluded', sa.Boolean(), nullable=False),
        sa.Column('composite', sa.Boolean(), nullable=False),
        sa.Column('component_of', sa.String(length=120), nullable=False),
        sa.Column('expanded_range', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_spec_item_canon_mark'), 'spec_item', ['canon_mark'])
    op.create_index('ix_spec_item_doc_mark', 'spec_item', ['document_id', 'canon_mark'])
    op.create_index(op.f('ix_spec_item_document_id'), 'spec_item', ['document_id'])


def downgrade():
    op.drop_table('spec_item')
    op.drop_table('sheet')
    op.drop_table('run')
    op.drop_table('document')
    op.drop_table('submission')
    op.drop_table('project')
    op.drop_table('org')
