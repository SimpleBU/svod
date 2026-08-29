"""паспорт тома и план проверки

Revision ID: 2a7d1f4c9b30
Revises: 111c88235453
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '2a7d1f4c9b30'
down_revision = '111c88235453'
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    # расхождения производны от разбора — лежат json-ом рядом с томом
    op.add_column('document', sa.Column('findings', JSONB, nullable=False,
                                        server_default=sa.text("'[]'")))

    op.create_table(
        'declared_sheet',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('no', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('revisions', JSONB, nullable=False),
        sa.Column('mark', sa.String(length=20), nullable=False),
        sa.Column('src_page', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_declared_sheet_document_id', 'declared_sheet', ['document_id'])

    op.create_table(
        'doc_ref',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('code', sa.String(length=200), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('sheets_declared', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('present', sa.Boolean(), nullable=False),
        sa.Column('src_page', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_doc_ref_document_id', 'doc_ref', ['document_id'])

    op.create_table(
        'norm_ref',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=120), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('replaced_by', sa.String(length=300), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('contextual', sa.Boolean(), nullable=False),
        sa.Column('sources', JSONB, nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_norm_ref_document_id', 'norm_ref', ['document_id'])

    op.create_table(
        'symbol',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('code', sa.String(length=60), nullable=False),
        sa.Column('page', sa.Integer(), nullable=False),
        sa.Column('image_key', sa.String(length=500), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_symbol_document_id', 'symbol', ['document_id'])

    op.create_table(
        'revision_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=True),
        sa.Column('sheets', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('doc_code', sa.String(length=200), nullable=False),
        sa.Column('basis', sa.Text(), nullable=False),
        sa.Column('src_page', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_revision_entry_document_id', 'revision_entry', ['document_id'])

    op.create_table(
        'check_plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('frozen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stats', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_check_plan_org_id', 'check_plan', ['org_id'])
    op.create_index('ix_check_plan_document_id', 'check_plan', ['document_id'])
    op.create_index('ix_check_plan_status', 'check_plan', ['status'])

    op.create_table(
        'check_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('spec_item_id', sa.Integer(), nullable=True),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('pos', sa.String(length=40), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('mark', sa.String(length=300), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False),
        sa.Column('qty', sa.Float(), nullable=True),
        sa.Column('page', sa.Integer(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('cls', sa.String(length=2), nullable=False),
        sa.Column('reasons', JSONB, nullable=False),
        sa.Column('verifiable_by', JSONB, nullable=False),
        sa.Column('evidence', JSONB, nullable=False),
        sa.Column('decision', sa.String(length=10), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['check_plan.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['spec_item_id'], ['spec_item.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_check_item_plan_id', 'check_item', ['plan_id'])
    op.create_index('ix_check_item_key', 'check_item', ['key'])
    op.create_index('ix_check_item_cls', 'check_item', ['cls'])
    op.create_index('ix_check_item_plan_cls', 'check_item', ['plan_id', 'cls'])

    op.create_table(
        'check_rule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('decision', sa.String(length=10), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('from_submission_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_check_rule_project_id', 'check_rule', ['project_id'])
    op.create_index('ix_check_rule_key', 'check_rule', ['key'])
    op.create_index('ix_check_rule_project_key', 'check_rule',
                    ['project_id', 'key'], unique=True)


def downgrade():
    for name in ('check_rule', 'check_item', 'check_plan', 'revision_entry',
                 'symbol', 'norm_ref', 'doc_ref', 'declared_sheet'):
        op.drop_table(name)
    op.drop_column('document', 'findings')
