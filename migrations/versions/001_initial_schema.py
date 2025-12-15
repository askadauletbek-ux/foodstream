"""initial schema

Revision ID: 001
Revises:
Create Date: 2025-12-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Restaurants
    op.create_table('restaurants',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('name', sa.String(), nullable=False),
                    sa.Column('slug', sa.String(), nullable=True),
                    sa.Column('table_count', sa.Integer(), nullable=True),
                    sa.Column('admin_secret_link', sa.String(), nullable=True),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('admin_secret_link')
                    )
    op.create_index(op.f('ix_restaurants_id'), 'restaurants', ['id'], unique=False)
    op.create_index(op.f('ix_restaurants_slug'), 'restaurants', ['slug'], unique=True)

    # 2. Users
    op.create_table('users',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('username', sa.String(), nullable=True),
                    sa.Column('password_hash', sa.String(), nullable=True),
                    sa.Column('role', sa.String(), nullable=True),
                    sa.Column('is_active', sa.Boolean(), nullable=True),
                    sa.Column('restaurant_id', sa.Integer(), nullable=True),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    # 3. Tables
    op.create_table('tables',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('restaurant_id', sa.Integer(), nullable=False),
                    sa.Column('number', sa.Integer(), nullable=False),
                    sa.Column('public_token', sa.String(), nullable=False),
                    sa.Column('is_active', sa.Boolean(), nullable=False),
                    sa.Column('created_at', sa.DateTime(), nullable=True),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_tables_id'), 'tables', ['id'], unique=False)
    op.create_index(op.f('ix_tables_public_token'), 'tables', ['public_token'], unique=True)
    op.create_index(op.f('ix_tables_restaurant_id'), 'tables', ['restaurant_id'], unique=False)

    # 4. Categories & Menu Items & Sliders & Signals
    op.create_table('categories',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('name', sa.String(), nullable=False),
                    sa.Column('sort_order', sa.Integer(), nullable=True),
                    sa.Column('is_active', sa.Boolean(), nullable=True),
                    sa.Column('restaurant_id', sa.Integer(), nullable=True),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_categories_id'), 'categories', ['id'], unique=False)

    op.create_table('menu_items',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('name', sa.String(), nullable=True),
                    sa.Column('description', sa.String(), nullable=True),
                    sa.Column('price', sa.Float(), nullable=True),
                    sa.Column('image_url', sa.String(), nullable=True),
                    sa.Column('sort_order', sa.Integer(), nullable=True),
                    sa.Column('is_active', sa.Boolean(), nullable=True),
                    sa.Column('stock', sa.Integer(), nullable=True),
                    sa.Column('restaurant_id', sa.Integer(), nullable=True),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_menu_items_id'), 'menu_items', ['id'], unique=False)
    op.create_index(op.f('ix_menu_items_name'), 'menu_items', ['name'], unique=False)

    op.create_table('menu_item_categories',
                    sa.Column('menu_item_id', sa.Integer(), nullable=False),
                    sa.Column('category_id', sa.Integer(), nullable=False),
                    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='CASCADE'),
                    sa.ForeignKeyConstraint(['menu_item_id'], ['menu_items.id'], ondelete='CASCADE'),
                    sa.PrimaryKeyConstraint('menu_item_id', 'category_id')
                    )

    op.create_table('slider_items',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('title', sa.String(), nullable=True),
                    sa.Column('description', sa.String(), nullable=True),
                    sa.Column('image_url', sa.String(), nullable=True),
                    sa.Column('sort_order', sa.Integer(), nullable=True),
                    sa.Column('is_active', sa.Boolean(), nullable=True),
                    sa.Column('restaurant_id', sa.Integer(), nullable=True),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_slider_items_id'), 'slider_items', ['id'], unique=False)

    op.create_table('service_signals',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('restaurant_id', sa.Integer(), nullable=True),
                    sa.Column('table_number', sa.Integer(), nullable=True),
                    sa.Column('is_active', sa.Boolean(), nullable=True),
                    sa.Column('created_at', sa.DateTime(), nullable=True),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_service_signals_id'), 'service_signals', ['id'], unique=False)
    op.create_index(op.f('ix_service_signals_restaurant_id'), 'service_signals', ['restaurant_id'], unique=False)

    # 5. Orders (Postgres ENUM)
    # Определяем Enum для Postgres, если его нет
    # В SQLite это будет VARCHAR
    order_status_enum = sa.Enum('BASKET_ASSEMBLY', 'REQUIRES_PAYMENT', 'VERIFICATION', 'PAYMENT_ERROR',
                                'IN_PROGRESS', 'DELIVERY', 'SUCCESSFULLY_DELIVERED', 'CANCELED',
                                name='order_status_enum', native_enum=True)

    op.create_table('orders',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('restaurant_id', sa.Integer(), nullable=False),
                    sa.Column('table_id', sa.Integer(), nullable=True),
                    sa.Column('table_number', sa.Integer(), nullable=True),
                    sa.Column('phone_number', sa.String(), nullable=True),
                    sa.Column('owner_token', sa.String(), nullable=True),
                    sa.Column('owner_name', sa.String(), nullable=True),
                    sa.Column('telegram_chat_id', sa.String(), nullable=True),
                    sa.Column('telegram_username', sa.String(), nullable=True),
                    sa.Column('is_bot_active', sa.Boolean(), nullable=True),
                    sa.Column('reminder_sent', sa.Boolean(), nullable=True),
                    sa.Column('last_activity', sa.DateTime(), nullable=True),
                    sa.Column('status', order_status_enum, nullable=True),
                    sa.Column('total_price', sa.Float(), nullable=True),
                    sa.Column('created_at', sa.DateTime(), nullable=True),
                    sa.Column('updated_at', sa.DateTime(), nullable=True),
                    sa.Column('waiter_id', sa.Integer(), nullable=True),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ondelete='CASCADE'),
                    sa.ForeignKeyConstraint(['table_id'], ['tables.id'], ondelete='SET NULL'),
                    sa.ForeignKeyConstraint(['waiter_id'], ['users.id'], ondelete='SET NULL'),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_orders_created_at'), 'orders', ['created_at'], unique=False)
    op.create_index(op.f('ix_orders_id'), 'orders', ['id'], unique=False)
    op.create_index(op.f('ix_orders_owner_token'), 'orders', ['owner_token'], unique=False)
    op.create_index(op.f('ix_orders_phone_number'), 'orders', ['phone_number'], unique=False)
    op.create_index(op.f('ix_orders_restaurant_id'), 'orders', ['restaurant_id'], unique=False)
    op.create_index(op.f('ix_orders_status'), 'orders', ['status'], unique=False)
    op.create_index(op.f('ix_orders_table_id'), 'orders', ['table_id'], unique=False)
    op.create_index(op.f('ix_orders_telegram_chat_id'), 'orders', ['telegram_chat_id'], unique=False)
    op.create_index(op.f('ix_orders_waiter_id'), 'orders', ['waiter_id'], unique=False)

    # 6. Order Items & Chat
    op.create_table('order_items',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('order_id', sa.Integer(), nullable=True),
                    sa.Column('menu_item_id', sa.Integer(), nullable=True),
                    sa.Column('quantity', sa.Integer(), nullable=True),
                    sa.Column('added_by', sa.String(), nullable=True),
                    sa.ForeignKeyConstraint(['menu_item_id'], ['menu_items.id'], ),
                    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_order_items_id'), 'order_items', ['id'], unique=False)

    op.create_table('chat_messages',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('order_id', sa.Integer(), nullable=True),
                    sa.Column('sender', sa.String(), nullable=True),
                    sa.Column('message_type', sa.String(), nullable=True),
                    sa.Column('content', sa.Text(), nullable=True),
                    sa.Column('timestamp', sa.DateTime(), nullable=True),
                    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_chat_messages_id'), 'chat_messages', ['id'], unique=False)

    # 7. Audit Log
    op.create_table('audit_logs',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('restaurant_id', sa.Integer(), nullable=True),
                    sa.Column('order_id', sa.Integer(), nullable=True),
                    sa.Column('actor_type', sa.String(), nullable=True),
                    sa.Column('actor_id', sa.String(), nullable=True),
                    sa.Column('action', sa.String(), nullable=True),
                    sa.Column('details', sa.Text(), nullable=True),
                    sa.Column('timestamp', sa.DateTime(), nullable=True),
                    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
                    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
                    sa.PrimaryKeyConstraint('id')
                    )
    op.create_index(op.f('ix_audit_logs_id'), 'audit_logs', ['id'], unique=False)
    op.create_index(op.f('ix_audit_logs_order_id'), 'audit_logs', ['order_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_restaurant_id'), 'audit_logs', ['restaurant_id'], unique=False)


def downgrade() -> None:
    # Удаление в обратном порядке
    op.drop_index(op.f('ix_audit_logs_restaurant_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_order_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_id'), table_name='audit_logs')
    op.drop_table('audit_logs')

    op.drop_index(op.f('ix_chat_messages_id'), table_name='chat_messages')
    op.drop_table('chat_messages')

    op.drop_index(op.f('ix_order_items_id'), table_name='order_items')
    op.drop_table('order_items')

    op.drop_index(op.f('ix_orders_waiter_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_telegram_chat_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_table_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_restaurant_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_phone_number'), table_name='orders')
    op.drop_index(op.f('ix_orders_owner_token'), table_name='orders')
    op.drop_index(op.f('ix_orders_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_created_at'), table_name='orders')
    op.drop_table('orders')

    # Drop Enum type in Postgres
    order_status_enum = sa.Enum(name='order_status_enum')
    order_status_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_service_signals_restaurant_id'), table_name='service_signals')
    op.drop_index(op.f('ix_service_signals_id'), table_name='service_signals')
    op.drop_table('service_signals')

    op.drop_index(op.f('ix_slider_items_id'), table_name='slider_items')
    op.drop_table('slider_items')

    op.drop_table('menu_item_categories')

    op.drop_index(op.f('ix_menu_items_name'), table_name='menu_items')
    op.drop_index(op.f('ix_menu_items_id'), table_name='menu_items')
    op.drop_table('menu_items')

    op.drop_index(op.f('ix_categories_id'), table_name='categories')
    op.drop_table('categories')

    op.drop_index(op.f('ix_tables_restaurant_id'), table_name='tables')
    op.drop_index(op.f('ix_tables_public_token'), table_name='tables')
    op.drop_index(op.f('ix_tables_id'), table_name='tables')
    op.drop_table('tables')

    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('users')

    op.drop_index(op.f('ix_restaurants_slug'), table_name='restaurants')
    op.drop_index(op.f('ix_restaurants_id'), table_name='restaurants')
    op.drop_table('restaurants')