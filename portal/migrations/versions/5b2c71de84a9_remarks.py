"""замечания: решение эксперта по расхождению

Revision ID: 5b2c71de84a9
Revises: 4e1b83af06c7
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '5b2c71de84a9'
down_revision = '4e1b83af06c7'
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.create_table(
        'remark',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('level', sa.String(length=10), nullable=False),
        sa.Column('subject', sa.String(length=300), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('sheets', JSONB, nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_remark_org_id', 'remark', ['org_id'])
    op.create_index('ix_remark_document_id', 'remark', ['document_id'])
    op.create_index('ix_remark_key', 'remark', ['key'])
    op.create_index('ix_remark_status', 'remark', ['status'])
    # одно решение на одно расхождение: повторное нажатие меняет статус,
    # а не заводит второе замечание
    op.create_index('ix_remark_doc_key', 'remark', ['document_id', 'key'],
                    unique=True)
    if op.get_bind().dialect.name == 'postgresql':
        op.create_foreign_key('fk_remark_author', 'remark', 'app_user',
                              ['author_id'], ['id'], ondelete='SET NULL')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_constraint('fk_remark_author', 'remark', type_='foreignkey')
    op.drop_table('remark')
