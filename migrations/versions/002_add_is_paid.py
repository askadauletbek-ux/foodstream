"""add is_paid to order_items

Revision ID: 002
Revises: 001
"""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'

def upgrade() -> None:
    op.add_column('order_items', sa.Column('is_paid', sa.Boolean(), nullable=True, server_default='0'))
    # Обновим существующие записи, чтобы они не были null
    op.execute("UPDATE order_items SET is_paid = 0")

def downgrade() -> None:
    op.drop_column('order_items', 'is_paid')