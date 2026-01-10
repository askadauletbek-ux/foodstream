
import os
import logging
import logging.config
import datetime
import secrets
import threading  # <--- ДОБАВЛЕНО
from urllib.parse import unquote
from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, url_for, send_file, session
from tasks import process_ai_message_task, check_reminders_task
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
from sqlalchemy.orm import joinedload
from sqlalchemy import text
from flask_socketio import SocketIO, emit, join_room, leave_room

import time
import asyncio
import models
import assistant
# create_db_and_tables убран из импорта
from models import SessionLocal, Order, MenuItem, OrderItem, OrderStatus, ChatMessage, Category, \
    SliderItem, Restaurant, User, ServiceSignal, Table, AuditLog
from utils_pdf import generate_qr_pdf
from ai_kitchen import ai_bp
from functools import wraps

# ИМПОРТ СЕРВИСОВ (Refactoring)
from services import (
    recalculate_order_total,
    log_audit,
    get_cart_text,
    find_item_by_name,
    execute_actions,
    resolve_table_by_token,
    get_or_create_cart
)

# --- RATE LIMITER (In-Memory) ---
# Структура: {ip: {endpoint: [timestamp1, timestamp2, ...]}}
request_history = {}


def check_rate_limit(limit=5, window=60):
    """Ограничивает вызовы endpoint: limit раз в window секунд для одного IP"""

    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.remote_addr
            endpoint = request.endpoint
            now = time.time()

            if ip not in request_history:
                request_history[ip] = {}
            if endpoint not in request_history[ip]:
                request_history[ip][endpoint] = []

            # Очистка старых записей
            request_history[ip][endpoint] = [t for t in request_history[ip][endpoint] if now - t < window]

            if len(request_history[ip][endpoint]) >= limit:
                return jsonify({"error": "Too many requests. Chill out."}), 429

            request_history[ip][endpoint].append(now)
            return f(*args, **kwargs)

        return wrapped

    return decorator


# --- Инициализация ---
load_dotenv()

# LOGGING CONFIG (Structured)
LOG_CONFIG = {
    'version': 1,
    'formatters': {
        'default': {'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',}
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'default', 'level': 'INFO'},
        'file': {'class': 'logging.FileHandler', 'filename': 'foodstream.log', 'formatter': 'default', 'level': 'WARNING'}
    },
    'root': {'level': 'INFO', 'handlers': ['console', 'file']}
}
logging.config.dictConfig(LOG_CONFIG)


app = Flask(__name__, template_folder='templates', static_folder='static')
app.register_blueprint(ai_bp)

# Делаем путь абсолютным относительно файла app.py
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')

# Инициализация сокетов (async_mode='eventlet' обязателен для продакшена)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_secret_key_change_in_prod_12345")

# --- SECURITY CONFIG ---
# Защита сессий и кук
app.config['SESSION_COOKIE_SECURE'] = False  # Важно: Поставьте True на продакшене с HTTPS!
app.config['SESSION_COOKIE_HTTPONLY'] = True # Защита от кражи кук через JS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Защита от CSRF

# Секретный путь админки из ENV
SUPER_ADMIN_PATH = os.getenv("ADMIN_PATH", "/super-admin")

# Отключение кеширования для админских панелей
@app.after_request
def add_security_headers(response):
    # Применяем заголовки только для админки и супер-админки
    if request.path.startswith('/admin') or request.path.startswith(SUPER_ADMIN_PATH):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response
# --- LOGIN MANAGER ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    with SessionLocal() as db:
        # FIX: Eagerly load 'restaurant' to prevent DetachedInstanceError
        return db.query(User).options(joinedload(User.restaurant)).get(int(user_id))


def get_status_enum_by_value(value_str):
    for status in OrderStatus:
        if status.value == value_str:
            return status
    return None


# --- ROUTES ---

@app.route('/')
def landing():
    if current_user.is_authenticated:
        if current_user.role == 'super_admin': return redirect('/super-admin')
        # Here we access current_user.restaurant, so it must be loaded in load_user
        if current_user.role == 'admin': return redirect(f'/admin/{current_user.restaurant.admin_secret_link}')
        if current_user.role == 'waiter': return redirect('/waiter')
    return render_template('login.html')


# In-memory хранилище попыток входа {ip: {'count': int, 'time': float}}
login_attempts = {}


@app.route('/login', methods=['GET', 'POST'])
def login():
    # Anti-Bruteforce Check
    client_ip = request.remote_addr
    now = time.time()

    # Сброс счетчика, если прошло больше 5 минут
    if client_ip in login_attempts:
        if now - login_attempts[client_ip]['time'] > 300:
            del login_attempts[client_ip]

    if request.method == 'POST':
        # Блокировка после 5 неудачных попыток
        if client_ip in login_attempts and login_attempts[client_ip]['count'] >= 5:
            return render_template('login.html', error="Слишком много попыток. Подождите 5 минут.")

        username = request.form.get('username')
        password = request.form.get('password')

        with SessionLocal() as db:
            user = db.query(User).options(joinedload(User.restaurant)).filter_by(username=username).first()
            if user and user.check_password(password):
                # Успех - очищаем счетчик
                if client_ip in login_attempts: del login_attempts[client_ip]

                login_user(user)

                # Редиректы с учетом безопасного пути
                if user.role == 'super_admin': return redirect(SUPER_ADMIN_PATH)
                if user.role == 'admin': return redirect(f'/admin/{user.restaurant.admin_secret_link}')
                if user.role == 'waiter': return redirect('/waiter')
                return "Роль не распознана", 400

            # Неудача - увеличиваем счетчик
            attempt = login_attempts.get(client_ip, {'count': 0, 'time': now})
            attempt['count'] += 1
            attempt['time'] = now  # Обновляем время последней попытки
            login_attempts[client_ip] = attempt

            return render_template('login.html', error="Неверные учетные данные")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/')


# --- SUPER ADMIN PANEL ---
@app.route(SUPER_ADMIN_PATH, methods=['GET', 'POST'])
@login_required
def super_admin_panel():
    if current_user.role != 'super_admin': return "Access Denied", 403

    msg = ""
    with SessionLocal() as db:
        if request.method == 'POST':
            # Логика создания ресторана
            action = request.form.get('action')

            if action == 'create':
                secret = secrets.token_urlsafe(16)
                if db.query(Restaurant).filter_by(slug=request.form['slug']).first():
                    msg = "Ошибка: Такой Slug уже занят!"
                else:
                    rest = Restaurant(
                        name=request.form['name'],
                        slug=request.form['slug'],
                        table_count=int(request.form['table_count']),
                        admin_secret_link=secret
                    )
                    db.add(rest)
                    db.commit()

                    admin_user = User(
                        username=request.form['admin_username'],
                        role='admin',
                        restaurant_id=rest.id
                    )
                    admin_user.set_password(request.form['admin_password'])
                    db.add(admin_user)
                    db.commit()
                    msg = f"Ресторан '{rest.name}' успешно создан!"

            elif action == 'delete':
                rest_id = request.form.get('restaurant_id')
                rest = db.query(Restaurant).get(rest_id)
                if rest:
                    # Каскадное удаление (упрощенно, в проде нужны constraints)
                    db.query(User).filter(User.restaurant_id == rest.id).delete()
                    db.delete(rest)
                    db.commit()
                    msg = "Ресторан удален."

        # Получаем список всех ресторанов для дашборда
        restaurants = db.query(Restaurant).all()
        return render_template('super_admin.html', msg=msg, restaurants=restaurants)


# --- CLIENT FACING ---

@app.route("/r/<identifier>")
def restaurant_index(identifier):
    with SessionLocal() as db:
        rest = None
        # Проверяем, это числовой ID или текстовый slug
        if identifier.isdigit():
            rest = db.query(Restaurant).get(int(identifier))
        else:
            rest = db.query(Restaurant).filter_by(slug=identifier).first()

        if not rest: return "Ресторан не найден", 404
        # Важно: передаем в шаблон реальный числовой ID (rest.id), чтобы API работало корректно
        return render_template("index.html", restaurant_id=rest.id, restaurant_name=rest.name)

@app.route("/api/r/<int:restaurant_id>/menu")
def get_restaurant_menu(restaurant_id):
    with SessionLocal() as db:
        items = db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id).all()
        return jsonify([{
            "id": i.id, "name": i.name, "description": i.description,
            "price": i.price, "image_url": i.image_url,
            "category": i.categories[0].name if i.categories else "Разное",
            "stock": i.stock
        } for i in items])


@app.route("/api/r/<int:restaurant_id>/slider")
def get_restaurant_slider(restaurant_id):
    with SessionLocal() as db:
        items = db.query(SliderItem).filter(SliderItem.restaurant_id == restaurant_id).all()
        return jsonify(
            [{"id": i.id, "title": i.title, "description": i.description, "image_url": i.image_url} for i in items])



@app.route("/api/cart", methods=['GET'])
def get_cart_state():
    rest_id = request.args.get('restaurant_id')
    table_token = request.args.get('table_token') # Теперь ожидаем токен

    # Получаем данные текущего пользователя из заголовков
    guest_token = request.headers.get('Guest-Token')
    raw_name = request.headers.get('Guest-Name')
    guest_name = unquote(raw_name) if raw_name else None

    if not rest_id or not table_token: return jsonify({"error": "Missing params"}), 400

    with SessionLocal() as db:
        cart, error = get_or_create_cart(db, rest_id, table_token, guest_token, guest_name)
        if error: return jsonify({"error": error}), 404

        items_data = {}
        for item in cart.items:
            # Определяем, напиток ли это (для фронтенда)
            is_drink = any(c.name.lower() in ['напитки', 'drinks', 'bar'] for c in item.menu_item.categories)

            # Формируем уникальный ключ для группировки в корзине не только по ID блюда, но и по автору?
            # Для простоты пока оставим группировку по ID, но будем выводить список имен
            # НО: в текущей архитектуре фронта cart - это словарь item_id -> data.
            # Упростим: последнее имя добавившего или список.

            # Лучше модифицировать объект, чтобы фронт мог отобразить "Вася, Петя"

            if item.menu_item_id in items_data:
                # ИСПРАВЛЕНО: Суммируем количество, если блюдо уже есть в словаре
                items_data[item.menu_item_id]["quantity"] += item.quantity
            else:
                items_data[item.menu_item_id] = {
                    "id": item.menu_item_id,
                    "name": item.menu_item.name,
                    "price": item.menu_item.price,
                    "quantity": item.quantity,
                    "image_url": item.menu_item.image_url,
                    "is_drink": is_drink,
                    "added_by": item.added_by
                }

        # Проверяем, является ли текущий юзер владельцем
        is_owner = (cart.owner_token == guest_token) if cart.owner_token else True

        # Возвращаем также статус заказа, чтобы фронт знал, можно ли менять еду
        return jsonify({
            "order_id": cart.id,  # ID для отмены
            "items": items_data,
            "status": cart.status.value,
            "status_key": cart.status.name,
            "owner_name": cart.owner_name,  # Имя владельца заказа
            "is_owner": is_owner  # Можно ли этому юзеру жать кнопку "Заказать"
        })


@app.route("/api/cart/update", methods=['POST'])
@check_rate_limit(limit=100, window=60)
def update_cart_item():
    data = request.json
    rest_id = int(data.get('restaurant_id'))
    table_token = data.get('table_token')
    item_id = data.get('item_id')
    action = data.get('action')

    guest_token = request.headers.get('Guest-Token')
    raw_name = request.headers.get('Guest-Name')
    guest_name = unquote(raw_name) if raw_name else "Guest"

    if not guest_token: return jsonify({"error": "Auth required"}), 401

    with SessionLocal() as db:
        cart, error = get_or_create_cart(db, rest_id, table_token, guest_token, guest_name)
        if error: return jsonify({"error": error}), 404

        menu_item = db.query(MenuItem).get(item_id)
        if not menu_item or menu_item.restaurant_id != rest_id:
            return jsonify({"error": "Item error"}), 404

        # 1. Определение типа продукта и прав доступа
        # Используем Eager Loading в модели или доступ через свойство, тут categories уже должны быть доступны
        is_drink = any(c.name.lower() in ['напитки', 'drinks', 'bar', 'бар'] for c in menu_item.categories)
        status = cart.status

        # 2. ЖЕСТКИЕ ПРАВИЛА ЖИЗНЕННОГО ЦИКЛА
        # BASKET_ASSEMBLY: Разрешено все.
        # ОСТАЛЬНЫЕ СТАТУСЫ:
        #   - Еда: ЗАПРЕЩЕНО любое изменение (add/remove). Только через официанта (через админку).
        #   - Напитки: Разрешено ДОБАВЛЕНИЕ (дозаказ). УДАЛЕНИЕ запрещено (вдруг бармен уже налил).

        allow_modification = False

        if status == OrderStatus.BASKET_ASSEMBLY:
            allow_modification = True
        elif status in [OrderStatus.IN_PROGRESS, OrderStatus.DELIVERY, OrderStatus.VERIFICATION,
                            OrderStatus.REQUIRES_PAYMENT]:
            # Заказ уже в работе.
            # РАЗРЕШАЕМ дозаказ (action == 'add') любых позиций (и еды, и напитков)
            if action == 'add':
                allow_modification = True
            else:
                allow_modification = False  # Удаление всё еще запрещено (вдруг уже готовят)
        else:
            # Completed / Canceled
            allow_modification = False

        if not allow_modification:
            return jsonify({"error": "Изменения запрещены на этой стадии. Зовите официанта."}), 409

        # 3. Выполнение изменения (Atomic Logic)
        audit_detail = f"{action.upper()} {menu_item.name}"

        if action == 'add':
            # ИСПРАВЛЕНО: Приводим ID к числу и ищем только НЕОПЛАЧЕННУЮ позицию
            item_id_int = int(item_id)
            existing = db.query(OrderItem).filter_by(order_id=cart.id, menu_item_id=item_id_int, is_paid=False).first()
            current_qty = existing.quantity if existing else 0

            if menu_item.stock is not None:
                if menu_item.stock <= 0 or (current_qty + 1) > menu_item.stock:
                    return jsonify({"error": "Товар закончился"}), 409

            if existing:
                existing.quantity += 1
                existing.added_by = guest_name
            else:
                # ИСПРАВЛЕНО: Используем append к коллекции, чтобы recalculate_order_total увидел новый товар сразу
                new_item = OrderItem(menu_item_id=item_id_int, quantity=1, added_by=guest_name, is_paid=False)
                cart.items.append(new_item)

            audit_detail += f" (New Qty: {current_qty + 1})"



        elif action == 'remove':

            existing = db.query(OrderItem).filter_by(order_id=cart.id, menu_item_id=item_id).first()

            if existing:

                if existing.quantity > 1:

                    existing.quantity -= 1

                    audit_detail += f" (New Qty: {existing.quantity})"

                else:

                    db.delete(existing)

                    audit_detail += " (Deleted)"

            else:

                return jsonify({"error": "Item not in cart"}), 404

            # Блок вынесен на уровень выше (убран лишний отступ):

        db.flush()

        recalculate_order_total(db, cart)

        log_audit(db, rest_id, 'cart_update', audit_detail, 'guest', guest_token, cart.id)

        db.commit()

        # Уведомляем клиента
        socketio.emit('cart_updated', {'total': cart.total_price}, room=table_token)

        # Уведомляем персонал (в общую комнату ресторана)
        # Если заказ уже активен (не черновик), шлем шумный сигнал 'new_order', иначе - тихий 'cart_updated'
        if cart.status != OrderStatus.BASKET_ASSEMBLY:
            socketio.emit('new_order', {'order_id': cart.id, 'table': table_token}, room=f"rest_{rest_id}")
        else:
            socketio.emit('cart_updated', {'order_id': cart.id, 'table': table_token}, room=f"rest_{rest_id}")

    return jsonify({"success": True, "total": cart.total_price})


@app.route("/api/cart/reset", methods=['POST'])
def reset_table_order():
    data = request.json
    rest_id = data.get('restaurant_id')
    table_token = data.get('table_token')

    guest_token = request.headers.get('Guest-Token')
    guest_name = unquote(request.headers.get('Guest-Name', 'Guest'))

    with SessionLocal() as db:
        table_obj, error = resolve_table_by_token(db, rest_id, table_token)
        if error: return jsonify({"error": error}), 404

        old_order = db.query(Order).filter(
            Order.restaurant_id == rest_id,
            Order.table_id == table_obj.id,
            Order.status.notin_([OrderStatus.CANCELED, OrderStatus.SUCCESSFULLY_DELIVERED])
        ).first()

        if old_order:
            # ПРАВИЛА СБРОСА:
            # 1. Если статус BASKET_ASSEMBLY -> Владелец может сбросить.
            # 2. Если статус > BASKET -> Сброс ЗАПРЕЩЕН гостю (только через официанта).
            # 3. Таймаут: Если last_activity > 2 часов -> можно сбросить любому (старый висяк).

            can_reset = False
            is_stale = False
            if old_order.last_activity:
                diff = datetime.datetime.now(datetime.timezone.utc) - old_order.last_activity.replace(
                    tzinfo=datetime.timezone.utc)
                if diff.total_seconds() > 7200:  # 2 часа
                    is_stale = True

            if is_stale:
                can_reset = True
            elif old_order.status == OrderStatus.BASKET_ASSEMBLY:
                if old_order.owner_token == guest_token:
                    can_reset = True
                else:
                    return jsonify({"error": "Стол занят. Попросите владельца заказа сбросить его."}), 403
            else:
                return jsonify({"error": "Заказ уже готовится. Сброс только через официанта."}), 403

            if can_reset:
                old_order.status = OrderStatus.CANCELED
                log_audit(db, rest_id, 'order_reset', f"Reset by {guest_name} (Stale: {is_stale})", 'guest',
                          guest_token, old_order.id)
                db.flush()

        # Создаем новый
        new_order = Order(
            restaurant_id=rest_id,
            table_id=table_obj.id,
            table_number=table_obj.number,
            status=OrderStatus.BASKET_ASSEMBLY,
            is_bot_active=True,
            owner_token=guest_token,
            owner_name=guest_name
        )
        db.add(new_order)
        db.commit()

        return jsonify({"success": True})


@app.route("/api/orders/cancel", methods=['POST'])
def cancel_order_endpoint():
    data = request.json
    order_id = data.get('order_id')
    guest_token = request.headers.get('Guest-Token')

    with SessionLocal() as db:
        order = db.query(Order).get(order_id)
        if not order: return jsonify({"error": "Order not found"}), 404

        # Проверка прав (только владелец)
        if order.owner_token and order.owner_token != guest_token:
            return jsonify({"error": "Только организатор может отменить заказ"}), 403

        # Проверка статуса (Бэкенд защита)
        # Если статус "Готовится" (IN_PROGRESS) или дальше - отмена запрещена
        if order.status in [OrderStatus.IN_PROGRESS, OrderStatus.DELIVERY, OrderStatus.SUCCESSFULLY_DELIVERED]:
            return jsonify({"error": "Заказ уже на кухне. Отмена через официанта."}), 400

        order.status = OrderStatus.CANCELED
        db.commit()

        # Уведомляем админ-панель об отмене заказа для проигрывания звука
        socketio.emit('status_change', {
            'order_id': order.id,
            'status': order.status.value
        }, room=f"rest_{order.restaurant_id}")

        return jsonify({"success": True})


@app.route("/orders/", methods=['POST'])
@check_rate_limit(limit=3, window=60)  # Защита от спама заказами
def create_order_api():
    data = request.json
    restaurant_id = data.get('restaurant_id')

    # Гость шлет token, Официант шлет number (через POS интерфейс)
    table_token = data.get('table_token')
    table_number = int(data.get('table_number')) if data.get('table_number') else None

    guest_token = request.headers.get('Guest-Token')

    waiter_id = None
    if current_user.is_authenticated and current_user.role == 'waiter':
        waiter_id = current_user.id
        if not restaurant_id: restaurant_id = current_user.restaurant_id
        # Официанту доверяем создание по номеру
    else:
        # Гость ОБЯЗАН иметь токен
        if not table_token: return jsonify({"error": "Guest must use table token"}), 400

    with SessionLocal() as db:
        if waiter_id:
            # Логика POS официанта (осталась прежней, но с проверкой принадлежности стола)
            # В реальном коде стоит найти стол по номеру и ID ресторана
            table_obj = db.query(Table).filter_by(restaurant_id=restaurant_id, number=table_number).first()
            if not table_obj: return jsonify({"error": "Table not found"}), 404

            order = Order(
                restaurant_id=restaurant_id,
                status=OrderStatus.REQUIRES_PAYMENT,
                table_id=table_obj.id,
                table_number=table_obj.number,
                waiter_id=waiter_id
            )
            db.add(order)
            db.flush()

            # Логика добавления из POS (массив items)
            total = 0
            for i_data in data.get('items', []):
                item = db.query(MenuItem).get(i_data['menu_item_id'])
                if item:
                    # Списание остатков
                    if item.stock is not None:
                        if item.stock < i_data['quantity']:
                            return jsonify({"error": f"{item.name}: мало остатка"}), 409
                        item.stock -= i_data['quantity']

                    db.add(OrderItem(order_id=order.id, menu_item_id=item.id, quantity=i_data['quantity']))
                    total += item.price * i_data['quantity']
            order.total_price = total


        else:

            # Для клиента: ищем по токену и проверяем владельца

            table_obj, error = resolve_table_by_token(db, restaurant_id, table_token)

            if error: return jsonify({"error": error}), 404

            order = db.query(Order).filter(

                Order.restaurant_id == restaurant_id,

                Order.table_id == table_obj.id,

                Order.status == OrderStatus.BASKET_ASSEMBLY

            ).first()

            if not order or not order.items:
                return jsonify({"error": "Корзина пуста"}), 400

            # SECURITY: Только владелец может нажать "Оформить"

            if order.owner_token and order.owner_token != guest_token:
                return jsonify({"error": "Только инициатор заказа может отправить его на кухню"}), 403

                # Финальная проверка остатков и Атомарный пересчет
                # Блокировку строк (with_for_update) опустим для простоты SQLite, полагаемся на transaction

            for order_item in order.items:
                m_item = order_item.menu_item
                if m_item.stock is not None:
                    if m_item.stock < order_item.quantity:
                        return jsonify({"error": f"{m_item.name}: закончился при оформлении!"}), 409
                    m_item.stock -= order_item.quantity

                # Принудительный пересчет перед финализацией
            total = recalculate_order_total(db, order)

            order.status = OrderStatus.REQUIRES_PAYMENT
            order.phone_number = data.get('phone_number')

            log_audit(db, restaurant_id, 'order_created', f"Total: {total}", 'guest', guest_token, order.id)

            db.commit()
            # Генерируем сигнал о ПЕРВИЧНОЙ отправке заказа (со звуком)
            socketio.emit('new_order', {'order_id': order.id, 'table': table_token or table_number},
                          room=f"rest_{restaurant_id}")

            return jsonify({"id": order.id, "total_price": order.total_price, "status": order.status.value})

# --- SERVICE SIGNALS ---

@app.route("/api/signal/call", methods=['POST'])
@check_rate_limit(limit=1, window=300)  # 1 вызов в 5 минут
def call_waiter_signal():
    data = request.json
    table_token = data.get('table_token')

    with SessionLocal() as db:
        # Резолвим токен для безопасности (чтобы не спамили на чужие столы)
        table_obj, error = resolve_table_by_token(db, data['restaurant_id'], table_token)
        if error: return jsonify({"error": error}), 400

        exists = db.query(ServiceSignal).filter_by(
            restaurant_id=data['restaurant_id'],
            table_number=table_obj.number,
            is_active=True
        ).first()
        if not exists:
            db.add(ServiceSignal(restaurant_id=data['restaurant_id'], table_number=table_obj.number))
            db.commit()
            # Моментальный сигнал официанту
            socketio.emit('new_signal', {'table': table_obj.number}, room=f"rest_{data['restaurant_id']}")
    return jsonify({"status": "ok"})


@app.route("/api/signal/resolve", methods=['POST'])
@login_required
def resolve_signal():
    data = request.json
    with SessionLocal() as db:
        sig = db.query(ServiceSignal).get(data['signal_id'])
        if sig and sig.restaurant_id == current_user.restaurant_id:
            sig.is_active = False

            log_audit(db, current_user.restaurant_id, 'signal_resolved',
                      f"Table {sig.table_number}",
                      current_user.role, current_user.id)

            db.commit()
        else:
            return jsonify({"error": "Forbidden"}), 403
    return jsonify({"success": True})

# --- AI CHAT CORE ---

@app.route("/api/telegram/bind", methods=['POST'])
def bind_telegram():
    """Связывает Telegram chat_id с заказом через token стола"""
    data = request.json
    token = data.get('token')  # table.public_token
    chat_id = data.get('chat_id')

    with SessionLocal() as db:
        # 1. Ищем стол по токену
        table = db.query(Table).filter_by(public_token=token).first()
        if not table:
            return jsonify({"error": "Invalid token"}), 404

        # 2. Ищем активный заказ на этом столе
        order = db.query(Order).filter(
            Order.table_id == table.id,
            Order.status == OrderStatus.BASKET_ASSEMBLY
        ).first()

        # 3. Если заказа нет - создаем
        if not order:
            order = Order(
                restaurant_id=table.restaurant_id,
                table_id=table.id,
                table_number=table.number,
                status=OrderStatus.BASKET_ASSEMBLY,
                is_bot_active=True
            )
            db.add(order)

        # 4. Привязываем Telegram
        order.telegram_chat_id = str(chat_id)
        db.commit()

        return jsonify({"success": True, "restaurant_name": table.restaurant.name, "table": table.number})


@app.route("/api/chat", methods=['POST'])
def chat_endpoint():
    data = request.json
    user_msg = data.get('message')
    # Теперь для Telegram мы используем только chat_id, привязка через БД
    chat_id = str(data.get('telegram_chat_id', 'anon'))
    is_telegram = 'telegram_chat_id' in data and data['telegram_chat_id'] != 'anon'

    # Для веб-чата (React) передаем параметры как раньше
    restaurant_id = data.get('restaurant_id')
    table_token = data.get('table_token')  # Используем токен, а не номер!

    with SessionLocal() as db:
        order = None

        if is_telegram:
            # Ищем по привязке Telegram
            order = db.query(Order).filter(
                Order.telegram_chat_id == chat_id,
                Order.status == OrderStatus.BASKET_ASSEMBLY
            ).order_by(Order.id.desc()).first()
        elif table_token:
            # Ищем по токену стола (Веб)
            table_obj = db.query(Table).filter_by(public_token=table_token).first()
            if table_obj:
                order = db.query(Order).filter(
                    Order.table_id == table_obj.id,
                    Order.status == OrderStatus.BASKET_ASSEMBLY
                ).first()
                if not order:  # Создаем черновик для веб-чата
                    order = Order(
                        restaurant_id=table_obj.restaurant_id,
                        table_id=table_obj.id,
                        table_number=table_obj.number,
                        status=OrderStatus.BASKET_ASSEMBLY,
                        is_bot_active=True
                    )
                    db.add(order)
                    db.commit()

        if not order:
            return jsonify({"response": "Сначала отсканируйте QR код (для Telegram нажмите /start)."})

        # Сохраняем сообщение юзера
        db.add(ChatMessage(order_id=order.id, sender='user', content=user_msg))
        order.last_activity = datetime.datetime.now(datetime.timezone.utc)
        db.commit()

        if not order.is_bot_active:
            return jsonify({"status": "waiting_for_admin"})


        thread = threading.Thread(
            target=process_ai_message_task,
            kwargs={
                    "chat_id": order.telegram_chat_id if is_telegram else None,
                    "user_text": user_msg,
                    "order_id": order.id,
                    "restaurant_id": order.restaurant_id,
                    "is_telegram": is_telegram
                }
            )
        thread.start()

        return jsonify({"status": "queued", "response": "..."})

# --- ADMIN ROUTES ---

@app.route('/admin/<secret_link>')
@login_required
def restaurant_admin_entry(secret_link):
    # Строгая проверка роли
    if current_user.role != 'admin':
        return "Access Denied: Требуются права администратора", 403

    # Изоляция данных: Проверяем, что ссылка принадлежит именно ресторану текущего пользователя
    # Если current_user.restaurant еще не загружен (lazy load), это вызовет подгрузку или ошибку, если ресторана нет
    if not current_user.restaurant or current_user.restaurant.admin_secret_link != secret_link:
        return "Access Denied: Попытка доступа к чужому ресторану", 403

    return render_template('admin.html', restaurant_id=current_user.restaurant_id,
                           restaurant_name=current_user.restaurant.name, secret_link=secret_link)

@app.route('/api/orders/')
@login_required
def get_admin_orders():
    if current_user.role not in ['admin', 'waiter']: return 403
    with SessionLocal() as db:
        # FIX: Eager load waiter to prevent DetachedInstanceError
        orders = db.query(Order).options(joinedload(Order.waiter), joinedload(Order.items).joinedload(OrderItem.menu_item)).filter(Order.restaurant_id == current_user.restaurant_id).order_by(
            Order.id.desc()).limit(50).all()
        res = []
        for o in orders:
            res.append({
                "id": o.id, "table_number": o.table_number, "phone_number": o.phone_number,
                "total_price": o.total_price, "status": o.status.value,
                "waiter_name": o.waiter.username if o.waiter else None,
                "items": [{"name": i.menu_item.name, "quantity": i.quantity} for i in o.items]
            })
        return jsonify(res)


@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
@login_required
def update_order_status(order_id):
    if current_user.role not in ['admin', 'waiter']: return 403
    data = request.json
    with SessionLocal() as db:
        order = db.query(Order).get(order_id)
        if not order or order.restaurant_id != current_user.restaurant_id: return 404

        new_status_str = data['status']
        new_status_enum = get_status_enum_by_value(new_status_str)

        if not new_status_enum and new_status_str in OrderStatus.__members__:
            new_status_enum = OrderStatus[new_status_str]

        if new_status_enum:
            old_status = order.status.value
            order.status = new_status_enum

            log_audit(db, current_user.restaurant_id, 'status_change',
                      f"{old_status} -> {new_status_enum.value}",
                      current_user.role, current_user.id, order.id)

            db.commit()
            # Уведомляем персонал
            socketio.emit('status_change', {'order_id': order.id, 'status': order.status.value},
                          room=f"rest_{current_user.restaurant_id}")
            # Уведомляем клиента (если есть привязанный стол)
            if order.table:
                socketio.emit('status_change', {'status': order.status.value}, room=order.table.public_token)
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Invalid status"}), 400

@app.route('/api/orders/<int:order_id>/chat')
@login_required
def get_order_chat(order_id):
    if current_user.role not in ['admin', 'waiter']: return 403
    with SessionLocal() as db:
        order = db.query(Order).get(order_id)
        if not order or order.restaurant_id != current_user.restaurant_id: return 404

        messages = db.query(ChatMessage).filter(ChatMessage.order_id == order.id).order_by(ChatMessage.timestamp).all()
        return jsonify({
            "order_id": order.id,
            "customer_username": order.telegram_username or order.phone_number or f"Стол {order.table_number}",
            "is_bot_active": order.is_bot_active,
            "messages": [{
                "sender": m.sender,
                "content": m.content,
                "type": m.message_type,
                "timestamp": m.timestamp.isoformat()
            } for m in messages]
        })


@app.route('/api/orders/<int:order_id>/send_message', methods=['POST'])
@login_required
def admin_send_message(order_id):
    if current_user.role not in ['admin', 'waiter']: return 403
    data = request.json
    with SessionLocal() as db:
        order = db.query(Order).get(order_id)
        if not order or order.restaurant_id != current_user.restaurant_id: return 404

        # Сообщение от админа
        msg = ChatMessage(order_id=order.id, sender='admin', content=data['message'])
        db.add(msg)

        # Обычно, если админ пишет, бота лучше выключить, чтобы он не перебивал
        # order.is_bot_active = False

        db.commit()
        return jsonify({"success": True})


@app.route('/api/orders/<int:order_id>/toggle_bot', methods=['PUT'])
@login_required
def toggle_bot(order_id):
    if current_user.role not in ['admin', 'waiter']: return 403
    data = request.json
    with SessionLocal() as db:
        order = db.query(Order).get(order_id)
        if not order or order.restaurant_id != current_user.restaurant_id: return 404

        order.is_bot_active = data['is_bot_active']
        db.commit()
        return jsonify({"success": True})


@app.route('/api/staff/', methods=['GET', 'POST'])
@app.route('/api/staff/<int:user_id>', methods=['PUT', 'DELETE'])
@login_required
def staff_management(user_id=None):
    if current_user.role != 'admin': return 403
    with SessionLocal() as db:
        if request.method == 'GET':
            staff = db.query(User).filter(User.restaurant_id == current_user.restaurant_id, User.role == 'waiter').all()
            return jsonify([{"id": u.id, "username": u.username, "is_active": u.is_active} for u in staff])

        if request.method == 'POST':
            data = request.json
            if db.query(User).filter_by(username=data['username']).first(): return jsonify({"error": "Exists"}), 400
            new_waiter = User(username=data['username'], role='waiter', restaurant_id=current_user.restaurant_id,
                              is_active=True)
            new_waiter.set_password(data['password'])
            db.add(new_waiter)
            db.commit()
            return jsonify({"success": True})

        if request.method == 'PUT':
            u = db.query(User).get(user_id)
            if not u or u.restaurant_id != current_user.restaurant_id: return 404
            data = request.json
            if 'password' in data and data['password']: u.set_password(data['password'])
            if 'is_active' in data: u.is_active = data['is_active']
            db.commit()
            return jsonify({"success": True})

        if request.method == 'DELETE':
            u = db.query(User).get(user_id)
            if u and u.restaurant_id == current_user.restaurant_id:
                db.delete(u)
                db.commit()
                return jsonify({"success": True})
        return 404


# --- MENU & CATEGORY MANAGEMENT ---

def save_upload(file):
    if not file: return None
    ext = file.filename.split('.')[-1]
    filename = f"{secrets.token_urlsafe(8)}.{ext}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    file.save(path)
    return f"/static/uploads/{filename}"


@app.route('/api/categories/', methods=['GET', 'POST'])
@app.route('/api/categories/<int:cat_id>', methods=['PUT', 'DELETE'])
@login_required
def manage_categories(cat_id=None):
    if current_user.role != 'admin': return 403
    with SessionLocal() as db:
        if request.method == 'GET':
            cats = db.query(Category).filter_by(restaurant_id=current_user.restaurant_id).order_by(
                Category.sort_order).all()
            return jsonify(
                [{"id": c.id, "name": c.name, "sort_order": c.sort_order, "is_active": c.is_active} for c in cats])

        if request.method == 'POST':
            data = request.json
            new_cat = Category(name=data['name'], sort_order=data.get('sort_order', 0),
                               restaurant_id=current_user.restaurant_id)
            db.add(new_cat)
            db.commit()
            return jsonify({"success": True})

        cat = db.query(Category).filter_by(id=cat_id, restaurant_id=current_user.restaurant_id).first()
        if not cat: return 404

        if request.method == 'PUT':
            data = request.json
            cat.name = data.get('name', cat.name)
            cat.sort_order = data.get('sort_order', cat.sort_order)
            cat.is_active = data.get('is_active', cat.is_active)
            db.commit()
            return jsonify({"success": True})

        if request.method == 'DELETE':
            db.delete(cat)
            db.commit()
            return jsonify({"success": True})


@app.route('/api/menu/', methods=['GET', 'POST'])
@app.route('/api/menu/<int:item_id>', methods=['PUT', 'DELETE'])
@login_required
def manage_menu(item_id=None):
    if current_user.role != 'admin': return 403
    with SessionLocal() as db:
        if request.method == 'GET':
            items = db.query(MenuItem).filter_by(restaurant_id=current_user.restaurant_id).order_by(
                MenuItem.sort_order).all()
            return jsonify([{
                "id": i.id, "name": i.name, "description": i.description,
                "price": i.price, "image_url": i.image_url, "sort_order": i.sort_order,
                "is_active": i.is_active, "category_ids": [c.id for c in i.categories]
            } for i in items])

        if request.method == 'POST':
            data = request.form
            file = request.files.get('image')

            # Логика: если есть файл - грузим его, иначе смотрим, пришел ли URL от AI
            image_url = save_upload(file)
            if not image_url and data.get('image_url_direct'):
                image_url = data.get('image_url_direct')

            stock_val = int(data['stock']) if data.get('stock') else None
            new_item = MenuItem(
                name=data['name'], description=data.get('description'),
                price=float(data['price']), sort_order=int(data.get('sort_order', 0)),
                image_url=image_url, restaurant_id=current_user.restaurant_id,
                stock=stock_val
            )

            # Привязка категорий
            cat_ids = data.getlist('categories')  # Получаем список ID
            for cid in cat_ids:
                cat = db.query(Category).get(int(cid))
                if cat and cat.restaurant_id == current_user.restaurant_id:
                    new_item.categories.append(cat)

            db.add(new_item)
            db.commit()
            return jsonify({"success": True})

        item = db.query(MenuItem).filter_by(id=item_id, restaurant_id=current_user.restaurant_id).first()
        if not item: return 404

        if request.method == 'PUT':
            # Для простоты JSON update (без смены картинки) или Form Data (со сменой)
            # Здесь упростим: если JSON - обновляем поля, картинку не трогаем
            data = request.json
            if data:
                item.name = data.get('name', item.name)
                item.price = float(data.get('price', item.price))
                item.description = data.get('description', item.description)
                item.sort_order = data.get('sort_order', item.sort_order)
                item.is_active = data.get('is_active', item.is_active)
                if 'image_url' in data:
                    item.image_url = data['image_url']

                if 'stock' in data: item.stock = data['stock']

                if 'categories' in data:
                    item.categories = []
                    for cid in data['categories']:
                        cat = db.query(Category).get(cid)
                        if cat: item.categories.append(cat)

                db.commit()
                return jsonify({"success": True})
            return 400

        if request.method == 'DELETE':
            db.delete(item)
            db.commit()
        return jsonify({"success": True})


@app.route('/api/slider/', methods=['GET', 'POST'])
@app.route('/api/slider/<int:slide_id>', methods=['DELETE'])
@login_required
def manage_slider(slide_id=None):
    if current_user.role != 'admin': return 403
    with SessionLocal() as db:
        if request.method == 'GET':
            slides = db.query(SliderItem).filter_by(restaurant_id=current_user.restaurant_id).all()
            return jsonify(
                [{"id": s.id, "title": s.title, "description": s.description, "image_url": s.image_url} for s in
                 slides])

        if request.method == 'POST':
            data = request.form
            file = request.files.get('image')
            image_url = save_upload(file)

            new_slide = SliderItem(
                title=data['title'], description=data.get('description'),
                image_url=image_url, restaurant_id=current_user.restaurant_id
            )
            db.add(new_slide)
            db.commit()
            return jsonify({"success": True})

        if request.method == 'DELETE':
            s = db.query(SliderItem).get(slide_id)
            if s and s.restaurant_id == current_user.restaurant_id:
                db.delete(s)
                db.commit()
                return jsonify({"success": True})
        return 404


@app.route('/api/admin/tables/status', methods=['GET'])
@login_required
def get_table_statuses():
    if current_user.role != 'admin': return 403
    with SessionLocal() as db:
        tables = db.query(models.Table).filter_by(restaurant_id=current_user.restaurant_id).order_by(
            models.Table.number).all()
        result = []
        for t in tables:
            active_order = db.query(Order).filter(
                Order.table_id == t.id,
                Order.status.notin_([OrderStatus.CANCELED, OrderStatus.SUCCESSFULLY_DELIVERED])
            ).first()

            # Получаем все позиции заказа с именами гостей
            items_detail = []
            if active_order:
                for oi in active_order.items:
                    items_detail.append({
                        "id": oi.id,
                        "name": oi.menu_item.name,
                        "quantity": oi.quantity,
                        "price": oi.menu_item.price,
                        "added_by": oi.added_by or "Гость",
                        "is_paid": oi.is_paid
                    })

            result.append({
                "id": t.id,
                "number": t.number,
                "active": active_order is not None,
                "order_id": active_order.id if active_order else None,
                "status": active_order.status.value if active_order else "Свободен",
                "created_at": active_order.created_at.strftime("%H:%M") if active_order else None,
                "items": items_detail  # Добавлено: состав корзины
            })
        return jsonify(result)


@app.route('/api/admin/orders/pay_cash', methods=['POST'])
@login_required
def pay_order_cash():
    if current_user.role != 'admin': return 403
    data = request.json
    order_id = data.get('order_id')
    item_ids = data.get('item_ids')  # Если пусто - оплата всего стола

    with SessionLocal() as db:
        order = db.query(Order).get(order_id)
        if not order: return 404

        if item_ids:
            # Частичная оплата конкретных позиций
            db.query(OrderItem).filter(OrderItem.id.in_(item_ids)).update({"is_paid": True}, synchronize_session=False)
        else:
            # Оплата всего стола
            db.query(OrderItem).filter(OrderItem.order_id == order_id).update({"is_paid": True},
                                                                              synchronize_session=False)
            order.status = OrderStatus.SUCCESSFULLY_DELIVERED

        db.commit()
        return jsonify({"success": True})

@app.route('/api/admin/tables/<int:table_id>/reset', methods=['POST'])
@login_required
def reset_table_admin(table_id):
    if current_user.role != 'admin': return 403
    with SessionLocal() as db:
        table = db.query(models.Table).get(table_id)
        if not table or table.restaurant_id != current_user.restaurant_id:
            return jsonify({"error": "Table not found"}), 404
        active_order = db.query(Order).filter(
            Order.table_id == table.id,
            Order.status.notin_([OrderStatus.CANCELED, OrderStatus.SUCCESSFULLY_DELIVERED])
        ).first()
        if active_order:
            active_order.status = OrderStatus.CANCELED
            log_audit(db, current_user.restaurant_id, 'admin_table_reset',
                      f"Table {table.number} reset by admin", 'admin', current_user.id, active_order.id)
            db.commit()
        return jsonify({"success": True})


@app.route('/api/settings', methods=['GET', 'POST'])  # Добавлена поддержка GET
@login_required
def update_settings():
    if current_user.role != 'admin': return 403

    with SessionLocal() as db:
        rest = db.query(Restaurant).get(current_user.restaurant_id)
        if not rest: return 404

        # Если запрашиваем данные при загрузке
        if request.method == 'GET':
            return jsonify({"table_count": rest.table_count})

        # Если сохраняем новые данные
        data = request.json
        new_count = int(data.get('table_count', rest.table_count))
        rest.table_count = new_count

        # Синхронизируем записи в таблице Tables
        # 1. Считаем текущие
        existing_tables = db.query(models.Table).filter(models.Table.restaurant_id == rest.id).count()

        # 2. Если стало больше - создаем новые
        if new_count > existing_tables:
            for i in range(existing_tables + 1, new_count + 1):
                # public_token генерируем, но в QR используем просто table number для простоты
                new_table = models.Table(
                    restaurant_id=rest.id,
                    number=i,
                    public_token=secrets.token_urlsafe(8)
                )
                db.add(new_table)

        # Если стало меньше - можно деактивировать, но пока просто оставляем как есть, чтобы не удалять данные

        db.commit()
        return jsonify({"success": True})


@app.route('/admin/download_qr')
@login_required
def download_qr_pdf_route():
    if current_user.role != 'admin': return 403
    rest = current_user.restaurant
    host_url = request.host_url.rstrip('/')

    # Получаем реальные объекты столов с токенами
    with SessionLocal() as db:
        tables = db.query(models.Table).filter(models.Table.restaurant_id == rest.id).all()
        # Генерируем PDF передавая список столов
        pdf_buffer = generate_qr_pdf(rest.name, rest.slug, tables, domain=host_url)

    return send_file(pdf_buffer, as_attachment=True, download_name=f"QR_{rest.slug}.pdf", mimetype='application/pdf')

# --- WAITER & WORKER --- (Стандартные, без изменений логики, только импорты)
@app.route('/waiter')
@login_required
def waiter_page():
    if current_user.role != 'waiter': return 403
    return render_template('waiter.html', restaurant_id=current_user.restaurant_id, waiter_name=current_user.username)


@app.route('/api/waiter/tables')
@login_required
def waiter_api_tables():
    if current_user.role != 'waiter': return 403

    try:
        with SessionLocal() as db:
            # AUTO-HEAL удален. Данные должны быть консистентны благодаря миграциям и Enum.

            orders = db.query(Order).options(
                joinedload(Order.items).joinedload(OrderItem.menu_item)
            ).filter(
                Order.restaurant_id == current_user.restaurant_id,
                Order.status.notin_([OrderStatus.CANCELED, OrderStatus.SUCCESSFULLY_DELIVERED]),
                Order.table_number.isnot(None)
            ).all()

            orders_info = []
            now = datetime.datetime.now(datetime.timezone.utc)

            for o in orders:
                if o.last_activity:
                    last_act = o.last_activity if o.last_activity.tzinfo else o.last_activity.replace(
                        tzinfo=datetime.timezone.utc)
                else:
                    last_act = now

                diff = int((now - last_act).total_seconds() / 60)

                items_list = []
                for i in o.items:
                    if i.menu_item:
                        items_list.append(f"{i.menu_item.name} x{i.quantity}")
                    else:
                        items_list.append(f"Удаленное блюдо x{i.quantity}")

                orders_info.append({
                    "id": o.id,
                    "table": o.table_number,
                    "status": o.status.value,
                    "total": o.total_price,
                    "minutes": diff,
                    "items": items_list
                })

            ai_hints = []
            try:
                # Убрали asyncio.run, так как функция теперь синхронная
                ai_hints = assistant.analyze_tables_for_waiter(orders_info)
            except Exception as e:
                pass
            signals = db.query(ServiceSignal).filter(
                ServiceSignal.restaurant_id == current_user.restaurant_id,
                ServiceSignal.is_active == True
            ).all()
            signals_data = [{"id": s.id, "table": s.table_number} for s in signals]

            return jsonify({"orders": orders_info, "hints": ai_hints, "signals": signals_data})

    except Exception as e:
        print(f"CRITICAL WAITER API ERROR: {e}")
        return jsonify({"error": str(e)}), 500



@app.route("/api/recommend", methods=['POST'])
def recommend_endpoint():
    data = request.json
    cart = data.get('cart', {})
    restaurant_id = data.get('restaurant_id')

    with SessionLocal() as db:
        # Получаем меню С КАТЕГОРИЯМИ, чтобы ИИ понимал, где напитки, а где еда
        items = db.query(MenuItem).options(joinedload(MenuItem.categories)).filter(
            MenuItem.restaurant_id == restaurant_id).all()

        menu_items = []
        for i in items:
            # Собираем названия категорий в строку (например: "Напитки, Бар")
            cat_str = ", ".join([c.name for c in i.categories]) if i.categories else "Разное"
            menu_items.append({
                "id": i.id,
                "name": i.name,
                "category": cat_str
            })

            # Спрашиваем AI
            try:
                # Убрали asyncio.run, так как функция теперь синхронная
                ai_data = assistant.get_upsell_recommendations(cart, menu_items)
            except Exception as e:
                print(f"Rec Error: {e}")
                return jsonify({"message": "", "items_data": []})

        rec_ids = ai_data.get('products', [])
        message = ai_data.get('message', '')

        if not rec_ids:
            return jsonify({"message": "", "items_data": []})

        # Получаем полные данные рекомендованных товаров
        items = db.query(MenuItem).filter(MenuItem.id.in_(rec_ids)).all()
        items_data = [{
            "id": i.id, "name": i.name, "price": i.price, "image_url": i.image_url
        } for i in items]

        return jsonify({"message": message, "items_data": items_data})


# ФОНОВЫЙ ПОТОК НАПОМИНАНИЙ (Вместо Celery Beat)
def background_scheduler():
    while True:
        try:
            check_reminders_task()
        except Exception as e:
            print(f"Scheduler Error: {e}")
        # Используем socketio.sleep для корректной передачи управления в eventlet
        socketio.sleep(120)
@app.route("/api/chat/history", methods=["GET"])
def chat_history_public():
    restaurant_id = request.args.get("restaurant_id")
    table_token = request.args.get("table_token")

    if not restaurant_id or not table_token:
        return jsonify({"messages": []})

    with SessionLocal() as db:
        table_obj = db.query(Table).filter_by(public_token=table_token).first()
        if not table_obj or str(table_obj.restaurant_id) != str(restaurant_id):
            return jsonify({"messages": []})

        order = db.query(Order).filter(
            Order.table_id == table_obj.id,
            Order.status == OrderStatus.BASKET_ASSEMBLY
        ).order_by(Order.id.desc()).first()

        if not order:
            return jsonify({"messages": []})

        messages = db.query(ChatMessage)\
            .filter(ChatMessage.order_id == order.id)\
            .order_by(ChatMessage.timestamp)\
            .all()

        return jsonify({
            "order_id": order.id,
            "messages": [{
                "sender": m.sender,
                "content": m.content,
                "type": m.message_type,
                "timestamp": m.timestamp.isoformat()
            } for m in messages]
        })

@socketio.on('join')
def on_join(data):
    """Клиент подписывается на обновления стола или ресторана"""
    room = data.get('room')
    join_room(room)

@socketio.on('leave')
def on_leave(data):
    room = data.get('room')
    leave_room(room)

if __name__ == "__main__":
    # Планировщик запускаем через встроенный механизм SocketIO
    socketio.start_background_task(background_scheduler)

    # ВАЖНО: debug=False для продакшена, используем socketio.run
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)