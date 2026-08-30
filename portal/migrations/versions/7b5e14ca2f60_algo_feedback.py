"""ложные срабатывания: обратная связь алгоритмам сверки и паспорта

Revision ID: 7b5e14ca2f60
Revises: 6a4f92c1d7e8
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '7b5e14ca2f60'
down_revision = '6a4f92c1d7e8'
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.create_table(
        'algo_feedback',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('submission_id', sa.Integer(), nullable=True),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=10), nullable=False,
                  server_default='match'),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('subject', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('reason', sa.String(length=40), nullable=False, server_default='other'),
        sa.Column('comment', sa.Text(), nullable=False, server_default=''),
        sa.Column('machine', JSONB, nullable=False, server_default='{}'),
        sa.Column('context', JSONB, nullable=False, server_default='{}'),
        sa.Column('author_id', sa.Integer(), nullable=True),
        sa.Column('withdrawn', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_algo_feedback_org_id', 'algo_feedback', ['org_id'])
    op.create_index('ix_algo_feedback_project_id', 'algo_feedback', ['project_id'])
    op.create_index('ix_algo_feedback_document_id', 'algo_feedback', ['document_id'])
    op.create_index('ix_algo_feedback_source', 'algo_feedback', ['source'])
    op.create_index('ix_algo_feedback_key', 'algo_feedback', ['key'])
    op.create_index('ix_algo_feedback_code', 'algo_feedback', ['code'])
    op.create_index('ix_algo_feedback_reason', 'algo_feedback', ['reason'])
    op.create_index('ix_algo_feedback_withdrawn', 'algo_feedback', ['withdrawn'])
    op.create_index('ix_algo_feedback_doc_key', 'algo_feedback',
                    ['document_id', 'key'], unique=True)
    # SQLite не умеет ALTER ... ADD CONSTRAINT — внешние ключи только там,
    # где они действительно живут (то же решение, что в миграции входа)
    if op.get_bind().dialect.name == 'postgresql':
        op.create_foreign_key('fk_algo_feedback_org', 'algo_feedback', 'org',
                              ['org_id'], ['id'], ondelete='CASCADE')
        op.create_foreign_key('fk_algo_feedback_project', 'algo_feedback', 'project',
                              ['project_id'], ['id'], ondelete='SET NULL')
        op.create_foreign_key('fk_algo_feedback_document', 'algo_feedback', 'document',
                              ['document_id'], ['id'], ondelete='SET NULL')
        op.create_foreign_key('fk_algo_feedback_author', 'algo_feedback', 'app_user',
                              ['author_id'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_table('algo_feedback')
