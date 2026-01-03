import datetime
import enum
import os
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, ForeignKey,
    Table, Enum as SQLAlchemyEnum, DateTime, Boolean, Text
)
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# --- Настройки БД ---
# Читаем из ENV, по умолчанию SQLite. Для Postgres: postgresql://user:pass@localhost/dbname
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./restaurant.db")

# check_same_thread нужен ТОЛЬКО для SQLite
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- Статусы Заказа ---
class OrderStatus(enum.Enum):
    BASKET_ASSEMBLY = "Сбор корзины"
    REQUIRES_PAYMENT = "Ожидает подтверждения"
    VERIFICATION = "На проверке"
    PAYMENT_ERROR = "Ошибка оплаты"
    IN_PROGRESS = "Готовится"
    DELIVERY = "Доставляется"
    SUCCESSFULLY_DELIVERED = "Успешно доставлен"
    CANCELED = "Отменен"


# --- Таблица связи (Блюда <-> Категории) ---
menu_item_categories = Table('menu_item_categories', Base.metadata,
                             Column('menu_item_id', Integer, ForeignKey('menu_items.id', ondelete="CASCADE"), primary_key=True),
                             Column('category_id', Integer, ForeignKey('categories.id', ondelete="CASCADE"), primary_key=True)
                             )


# --- НОВЫЕ МОДЕЛИ SAAS ---

class Restaurant(Base):
    __tablename__ = "restaurants"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True)
    table_count = Column(Integer, default=10)
    admin_secret_link = Column(String, unique=True)

    users = relationship("User", back_populates="restaurant")
    categories = relationship("Category", back_populates="restaurant")
    menu_items = relationship("MenuItem", back_populates="restaurant")
    orders = relationship("Order", back_populates="restaurant")
    slider_items = relationship("SliderItem", back_populates="restaurant")
    tables = relationship("Table", back_populates="restaurant", cascade="all, delete-orphan")


class Table(Base):
    __tablename__ = "tables"
    id = Column(Integer, primary_key=True, index=True)

    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True, nullable=False)
    restaurant = relationship("Restaurant", back_populates="tables")

    number = Column(Integer, nullable=False)
    public_token = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    orders = relationship("Order", back_populates="table")


class User(UserMixin, Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String)  # 'super_admin', 'admin', 'waiter'
    is_active = Column(Boolean, default=True)

    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=True)
    restaurant = relationship("Restaurant", back_populates="users")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# --- ОБНОВЛЕННЫЕ МОДЕЛИ (С restaurant_id) ---

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    restaurant_id = Column(Integer, ForeignKey("restaurants.id"))
    restaurant = relationship("Restaurant", back_populates="categories")

    menu_items = relationship("MenuItem", secondary=menu_item_categories, back_populates="categories")


class MenuItem(Base):
    __tablename__ = "menu_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(String)
    price = Column(Float)
    image_url = Column(String, nullable=True)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    # NEW: Учет остатков. None = бесконечно, 0 = стоп-лист, >0 = лимит
    stock = Column(Integer, nullable=True, default=None)

    restaurant_id = Column(Integer, ForeignKey("restaurants.id"))
    restaurant = relationship("Restaurant", back_populates="menu_items")

    categories = relationship("Category", secondary=menu_item_categories, back_populates="menu_items")


# NEW: Сигналы вызова официанта
class ServiceSignal(Base):
    __tablename__ = "service_signals"
    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True)
    table_number = Column(Integer)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

class SliderItem(Base):
    __tablename__ = "slider_items"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(String)
    image_url = Column(String)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    restaurant_id = Column(Integer, ForeignKey("restaurants.id"))
    restaurant = relationship("Restaurant", back_populates="slider_items")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)

    # CASCADE удаление: если ресторан удален, заказы тоже.
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), index=True, nullable=False)
    restaurant = relationship("Restaurant", back_populates="orders")

    # SET NULL: если стол удален, история заказов остается (просто без привязки к столу)
    table_id = Column(Integer, ForeignKey("tables.id", ondelete="SET NULL"), nullable=True, index=True)
    table = relationship("Table", back_populates="orders")

    table_number = Column(Integer, nullable=True)
    phone_number = Column(String, index=True, nullable=True)

    owner_token = Column(String, nullable=True, index=True) # Добавлен индекс для поиска по токену гостя
    owner_name = Column(String, nullable=True)

    telegram_chat_id = Column(String, index=True, nullable=True)
    telegram_username = Column(String, nullable=True)
    is_bot_active = Column(Boolean, default=True)
    reminder_sent = Column(Boolean, default=False)
    last_activity = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    # ВАЖНО: Именованный Enum для Postgres. native_enum=True создаст тип 'order_status_enum' в БД.
    status = Column(SQLAlchemyEnum(OrderStatus, name="order_status_enum", native_enum=True), default=OrderStatus.BASKET_ASSEMBLY, index=True)
    total_price = Column(Float, default=0.0)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    waiter_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    waiter = relationship("User", foreign_keys=[waiter_id])

    # Cascade здесь настроен через relationship, но в DB уровне тоже полезно добавить (см. OrderItem)
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="order", cascade="all, delete-orphan")

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"))
    quantity = Column(Integer, default=1)
    added_by = Column(String, nullable=True)
    is_paid = Column(Boolean, default=False) # Добавлено: флаг оплаты позиции

    order = relationship("Order", back_populates="items")
    menu_item = relationship("MenuItem")


# NEW: Аудит действий
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    actor_type = Column(String)  # 'guest', 'waiter', 'admin', 'system'
    actor_id = Column(String)  # guest_token или user_id
    action = Column(String)  # 'status_change', 'add_item', 'reset', 'resolve_signal'
    details = Column(Text)  # JSON или текст: "Old: X -> New: Y"

    timestamp = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    sender = Column(String)
    message_type = Column(String, default='text')
    content = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    order = relationship("Order", back_populates="chat_messages")

