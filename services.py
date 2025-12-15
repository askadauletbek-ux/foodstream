import datetime
from sqlalchemy.orm import joinedload
from models import Order, OrderItem, MenuItem, Table, AuditLog, OrderStatus, ServiceSignal

# --- HELPERS: CORE LOGIC ---

def recalculate_order_total(db, order):
    """Пересчитывает сумму заказа."""
    total = 0.0
    for item in order.items:
        total += item.menu_item.price * item.quantity
    order.total_price = total
    return total

def log_audit(db, rest_id, action, details, actor_type, actor_id, order_id=None):
    """Записывает действие в AuditLog"""
    log_entry = AuditLog(
        restaurant_id=rest_id,
        order_id=order_id,
        actor_type=actor_type,
        actor_id=str(actor_id),
        action=action,
        details=details
    )
    db.add(log_entry)

def get_cart_text(order):
    if not order or not order.items: return "Корзина пуста."
    summary = [f"- {i.menu_item.name} x{i.quantity}" for i in order.items]
    return "В КОРЗИНЕ:\n" + "\n".join(summary)

def find_item_by_name(db, name_query, restaurant_id):
    items = db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id).all()
    for i in items:
        if i.name.lower() == name_query.lower(): return i
    for i in items:
        if name_query.lower() in i.name.lower(): return i
    return None

def resolve_table_by_token(db, restaurant_id, table_token):
    table = db.query(Table).filter_by(public_token=table_token).first()
    if not table: return None, "Invalid table token"
    if table.restaurant_id != int(restaurant_id): return None, "Table error"
    if not table.is_active: return None, "Table inactive"
    return table, None

def get_or_create_cart(db, restaurant_id, table_token, guest_token=None, guest_name=None):
    table_obj, error = resolve_table_by_token(db, restaurant_id, table_token)
    if error: return None, error

    active_order = db.query(Order).filter(
        Order.restaurant_id == restaurant_id,
        Order.table_id == table_obj.id,
        Order.status.notin_([OrderStatus.CANCELED, OrderStatus.SUCCESSFULLY_DELIVERED])
    ).first()

    if not active_order:
        active_order = Order(
            restaurant_id=restaurant_id,
            table_id=table_obj.id,
            table_number=table_obj.number,
            status=OrderStatus.BASKET_ASSEMBLY,
            is_bot_active=True,
            owner_token=guest_token,
            owner_name=guest_name
        )
        db.add(active_order)
        db.commit()
    return active_order, None

def execute_actions(db, order, actions, restaurant_id):
    """
    Выполняет JSON-действия от AI.
    Теперь находится здесь, чтобы tasks.py мог ее импортировать без app.py.
    """
    if not actions or not isinstance(actions, list): return

    for action in actions:
        if isinstance(action, str): continue
        atype = action.get('type')
        item_name = action.get('item_name') or action.get('remove_name') or action.get('add_name')
        if not item_name: continue

        if atype == 'add_item':
            item = find_item_by_name(db, item_name, restaurant_id)
            if item:
                existing = next((i for i in order.items if i.menu_item_id == item.id), None)
                qty = action.get('quantity', 1)
                if existing: existing.quantity += qty
                else: db.add(OrderItem(order_id=order.id, menu_item_id=item.id, quantity=qty))

        elif atype == 'remove_item':
            item = find_item_by_name(db, item_name, restaurant_id)
            if item:
                existing = next((i for i in order.items if i.menu_item_id == item.id), None)
                if existing: db.delete(existing)

        elif atype == 'update_quantity':
            item = find_item_by_name(db, item_name, restaurant_id)
            if item:
                existing = next((i for i in order.items if i.menu_item_id == item.id), None)
                qty = action.get('quantity', 1)
                if existing: existing.quantity = qty
                elif qty > 0: db.add(OrderItem(order_id=order.id, menu_item_id=item.id, quantity=qty))

        elif atype == 'clear_cart':
            db.query(OrderItem).filter(OrderItem.order_id == order.id).delete()

    db.commit()
    db.refresh(order)
    recalculate_order_total(db, order)
    db.commit()