"""просмотрщик листов: картинки, якоря замечаний, места марок

Revision ID: 6a4f92c1d7e8
Revises: 5b2c71de84a9
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '6a4f92c1d7e8'
down_revision = '5b2c71de84a9'
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.add_column('document', sa.Column('pages_rendered', sa.Integer(),
                                        nullable=False, server_default='0'))
    op.add_column('match_item', sa.Column('anchors', JSONB, nullable=False,
                                          server_default='[]'))
    op.add_column('remark', sa.Column('page', sa.Integer(), nullable=True))
    op.add_column('remark', sa.Column('anchor', JSONB, nullable=False,
                                      server_default='{}'))
    op.add_column('remark', sa.Column('anchor_document_id', sa.Integer(),
                                      nullable=True))
    op.add_column('remark', sa.Column('anchor_label', sa.String(length=120),
                                      nullable=False, server_default=''))


def downgrade():
    op.drop_column('remark', 'anchor_label')
    op.drop_column('remark', 'anchor_document_id')
    op.drop_column('remark', 'anchor')
    op.drop_column('remark', 'page')
    op.drop_column('match_item', 'anchors')
    op.drop_column('document', 'pages_rendered')
