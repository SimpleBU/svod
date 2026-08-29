"""пользователи и авторство решений

Revision ID: 3c9e40b7d512
Revises: 2a7d1f4c9b30
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa

revision = '3c9e40b7d512'
down_revision = '2a7d1f4c9b30'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'app_user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=True),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('password_hash', sa.String(length=300), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_app_user_org_id', 'app_user', ['org_id'])
    op.create_index('ix_app_user_email', 'app_user', ['email'], unique=True)

    # автор решения: на этапе 3 у замечания должен быть автор
    op.add_column('check_item', sa.Column('decided_by', sa.Integer(), nullable=True))
    op.add_column('check_plan', sa.Column('frozen_by', sa.Integer(), nullable=True))
    # SQLite не умеет ALTER ... ADD CONSTRAINT, а на демо схема и так
    # создаётся из моделей, минуя миграции (см. portal/db.py)
    if op.get_bind().dialect.name == 'postgresql':
        op.create_foreign_key('fk_check_item_decided_by', 'check_item', 'app_user',
                              ['decided_by'], ['id'], ondelete='SET NULL')
        op.create_foreign_key('fk_check_plan_frozen_by', 'check_plan', 'app_user',
                              ['frozen_by'], ['id'], ondelete='SET NULL')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_constraint('fk_check_plan_frozen_by', 'check_plan', type_='foreignkey')
        op.drop_constraint('fk_check_item_decided_by', 'check_item', type_='foreignkey')
    op.drop_column('check_plan', 'frozen_by')
    op.drop_column('check_item', 'decided_by')
    op.drop_table('app_user')
