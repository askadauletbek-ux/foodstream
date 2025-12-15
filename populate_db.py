import secrets
import random
# УБРАЛИ create_db_and_tables из импорта
from models import SessionLocal, Category, MenuItem, SliderItem, Restaurant, User

# Ссылки на картинки-заглушки (чтобы сразу было красиво)
IMG_PIZZA = "https://images.unsplash.com/photo-1604382354936-07c5d9983bd3?auto=format&fit=crop&w=800&q=80"
IMG_SUSHI = "https://images.unsplash.com/photo-1579871494447-9811cf80d66c?auto=format&fit=crop&w=800&q=80"
IMG_BURGER = "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?auto=format&fit=crop&w=800&q=80"
IMG_DRINK = "https://images.unsplash.com/photo-1551024709-8f23befc6f87?auto=format&fit=crop&w=800&q=80"
IMG_BANNER_1 = "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=1200&q=80"
IMG_BANNER_2 = "https://images.unsplash.com/photo-1555939594-58d7cb561ad1?auto=format&fit=crop&w=1200&q=80"


def populate():
    # ВАЖНО: Мы больше не создаем таблицы здесь.
    # Перед запуском нужно выполнить в терминале: alembic upgrade head

    db = SessionLocal()

    print("🌱 Начинаю заполнение базы данных...")

    # --- 1. СОЗДАНИЕ СУПЕР-АДМИНА ---
    if not db.query(User).filter_by(role='super_admin').first():
        super_admin = User(username='root', role='super_admin')
        super_admin.set_password('root')
        db.add(super_admin)
        print("👤 Super Admin created: root / root")

    # --- 2. СОЗДАНИЕ ДЕМО-РЕСТОРАНА ---
    # Проверяем, есть ли ресторан, чтобы не дублировать
    demo_rest = db.query(Restaurant).filter_by(slug='demo').first()

    if not demo_rest:
        secret = secrets.token_urlsafe(10)
        demo_rest = Restaurant(
            name="FoodStream Demo",
            slug="demo",
            table_count=10,
            admin_secret_link=secret
        )
        db.add(demo_rest)
        db.flush()  # Чтобы получить ID ресторана

        # Админ ресторана
        rest_admin = User(username='admin', role='admin', restaurant=demo_rest)
        rest_admin.set_password('admin')
        db.add(rest_admin)

        # Официант
        waiter = User(username='waiter', role='waiter', restaurant=demo_rest)
        waiter.set_password('waiter')
        db.add(waiter)

        print(f"🍔 Demo Restaurant created.")
        print(f"👉 Admin Link: /admin/{secret}")
        print(f"👉 Menu Link: /r/{demo_rest.id}")
        print(f"👉 Waiter Login: waiter / waiter")
    else:
        print("⚠️ Ресторан 'demo' уже существует. Добавляем данные в него.")

    # Проверяем, есть ли уже категории в этом ресторане
    if db.query(Category).filter_by(restaurant_id=demo_rest.id).count() > 0:
        print("⚠️ Меню уже заполнено. Пропускаю.")
        db.commit()
        db.close()
        return

    # --- КАТЕГОРИИ (Привязываем к demo_rest) ---
    cat_sushi = Category(name="Суши", restaurant=demo_rest)
    cat_pizza = Category(name="Пицца", restaurant=demo_rest)
    cat_burgers = Category(name="Бургеры", restaurant=demo_rest)
    cat_drinks = Category(name="Напитки", restaurant=demo_rest)
    cat_sets = Category(name="Сеты", restaurant=demo_rest)

    db.add_all([cat_sushi, cat_pizza, cat_burgers, cat_drinks, cat_sets])
    db.commit()
    print("✅ Категории созданы")

    # --- БЛЮДА (Привязываем к demo_rest) ---
    items = [
        # Суши
        MenuItem(
            name="Филадельфия Лайт",
            description="Классический ролл с лососем, сливочным сыром и огурцом.",
            price=2400,
            image_url=IMG_SUSHI,
            categories=[cat_sushi, cat_sets],
            restaurant=demo_rest
        ),
        MenuItem(
            name="Калифорния с крабом",
            description="Снежный краб, авокадо, икра тобико, майонез.",
            price=2100,
            image_url="https://images.unsplash.com/photo-1611143669185-af224c5e3252?auto=format&fit=crop&w=800&q=80",
            categories=[cat_sushi],
            restaurant=demo_rest
        ),
        MenuItem(
            name="Запеченный с лососем",
            description="Теплый ролл под шапкой из сырного соуса с лососем.",
            price=2800,
            image_url="https://images.unsplash.com/photo-1635526910429-0414839e5593?auto=format&fit=crop&w=800&q=80",
            categories=[cat_sushi],
            restaurant=demo_rest
        ),

        # Пицца
        MenuItem(
            name="Пепперони",
            description="Пикантные колбаски пепперони, моцарелла, фирменный томатный соус.",
            price=3200,
            image_url=IMG_PIZZA,
            categories=[cat_pizza],
            restaurant=demo_rest
        ),
        MenuItem(
            name="Четыре Сыра",
            description="Моцарелла, чеддер, пармезан, дорблю. Сливочная основа.",
            price=3500,
            image_url="https://images.unsplash.com/photo-1573821663912-569905455b1c?auto=format&fit=crop&w=800&q=80",
            categories=[cat_pizza],
            restaurant=demo_rest
        ),
        MenuItem(
            name="Мясная",
            description="Ветчина, бекон, охотничьи колбаски, моцарелла, красный лук.",
            price=3800,
            image_url="https://images.unsplash.com/photo-1628840042765-356cda07504e?auto=format&fit=crop&w=800&q=80",
            categories=[cat_pizza],
            restaurant=demo_rest
        ),

        # Бургеры
        MenuItem(
            name="Чизбургер XL",
            description="Сочная говяжья котлета, двойной чеддер, маринованные огурчики.",
            price=2200,
            image_url=IMG_BURGER,
            categories=[cat_burgers],
            restaurant=demo_rest
        ),

        # Напитки
        MenuItem(
            name="Coca-Cola 1л",
            description="Освежающий газированный напиток.",
            price=600,
            image_url=IMG_DRINK,
            categories=[cat_drinks],
            restaurant=demo_rest
        ),
        MenuItem(
            name="Лимонад Домашний",
            description="Свежие лимоны, мята, лед. 0.5л",
            price=900,
            image_url="https://images.unsplash.com/photo-1513558161293-cdaf765ed2fd?auto=format&fit=crop&w=800&q=80",
            categories=[cat_drinks],
            restaurant=demo_rest
        ),
    ]

    db.add_all(items)
    db.commit()
    print(f"✅ Добавлено {len(items)} блюд")

    # --- СЛАЙДЕР (БАННЕРЫ) (Привязываем к demo_rest) ---
    sliders = [
        SliderItem(
            title="Скидка 20% на первый заказ",
            description="Попробуйте наши лучшие роллы по супер цене!",
            image_url=IMG_BANNER_1,
            restaurant=demo_rest
        ),
        SliderItem(
            title="Пицца в подарок!",
            description="При заказе двух больших пицц - Пепперони 25см бесплатно.",
            image_url=IMG_BANNER_2,
            restaurant=demo_rest
        )
    ]

    db.add_all(sliders)
    db.commit()
    print("✅ Баннеры добавлены")

    db.close()
    print("🚀 База данных успешно заполнена! Можно запускать сервер.")


if __name__ == "__main__":
    populate()