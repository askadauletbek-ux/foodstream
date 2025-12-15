import os
import logging
import json
import time # Используем time вместо asyncio
from openai import OpenAI # Используем синхронный клиент
from dotenv import load_dotenv

# --- Инициализация ---
load_dotenv()


# client удален отсюда

# --- Менеджер Напоминаний (Упрощенная заглушка, т.к. tasks.py обрабатывает это в фоне) ---
class ReminderManager:
    def __init__(self):
        self.tasks = {}

    def schedule_reminder(self, chat_id, callback, delay=120):
        # В синхронной/gevent версии мы полагаемся на фоновый воркер (check_reminders_task),
        # поэтому здесь можно просто ничего не делать или логировать.
        pass

    def cancel_reminder(self, chat_id):
        pass


reminder_manager = ReminderManager()


# --- Системный Промпт ---
def _get_system_prompt(menu_list_str, cart_context):
    return (
        f"Ты — мозг ресторана 'Nomi'. Твоя цель — ПРОДАВАТЬ через ДИАЛОГ.\n"
        f"МЕНЮ (ID: Название - Цена):\n{menu_list_str}\n"
        f"КОРЗИНА СЕЙЧАС: {cart_context}\n\n"

        f"Ты должен вернуть JSON с объектом: {{ \"actions\": [...], \"response\": \"...\", \"recommendations\": [...] }}\n"
        f"Поле 'actions' — это список изменений БД (строго по приказу).\n"
        f"Поле 'recommendations' — список предложений (id блюда + кол-во).\n\n"

        f"--- ЛОГИКА РЕКОМЕНДАЦИЙ ---\n"
        f"Если пользователь просит совет или описывает ситуацию (напр. 'Нас 5 человек') — НЕ делай 'actions'.\n"
        f"Вместо этого:\n"
        f"1. Заполни 'recommendations': [{{ \"id\": 12, \"quantity\": 2 }}, ...]\n"
        f"2. Напиши в 'response' продающий текст: 'Для такой компании советую взять 2 Пепперони и Колу!'\n\n"

        f"--- ГЛАВНОЕ ПРАВИЛО (БЕЗ САМОДЕЯТЕЛЬНОСТИ) ---\n"
        f"Если пользователь НЕ сказал 'добавь'/'беру'/'давай' — поле 'actions' должно быть ПУСТЫМ: [].\n\n"

        f"--- ДОСТУПНЫЕ ДЕЙСТВИЯ (В 'actions') ---\n"
        f"1. {{ \"type\": \"add_item\", \"item_name\": \"...\", \"quantity\": 1 }}\n"
        f"2. {{ \"type\": \"remove_item\", \"item_name\": \"...\" }}\n"
        f"3. {{ \"type\": \"update_quantity\", \"item_name\": \"...\", \"quantity\": 5 }}\n"
        f"4. {{ \"type\": \"clear_cart\" }}\n"
        f"5. Нет действий: []\n\n"

        f"--- ЛИЧНОСТЬ (NOMI) ---\n"
        f"Ты — дерзкий, но заботливый официант. Твой стиль: 'Я тут подумала...', 'Мой совет...'. Используй эмодзи (🍕, 😎)."
    )


def process_message(user_text, cart, menu_items, chat_history=None):
    # СОЗДАЕМ СИНХРОННОГО КЛИЕНТА
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    menu_names = [f"{m['id']}: {m['name']} ({m['price']}тг)" for m in menu_items]
    menu_str = "\n".join(menu_names)

    id_to_name = {str(m['id']): m['name'] for m in menu_items}
    cart_ctx = ", ".join([f"{id_to_name.get(k, 'Неизв.')} ({v} шт)" for k, v in cart.items()]) if cart else "Пусто"

    system_prompt = _get_system_prompt(menu_str, cart_ctx)

    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        messages.extend(chat_history[-6:])
    messages.append({"role": "user", "content": user_text})

    try:
        # Убрали await
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.7
        )
        content = response.choices[0].message.content
        data = json.loads(content)

        if not isinstance(data.get('actions'), list): data['actions'] = []
        if not isinstance(data.get('recommendations'), list): data['recommendations'] = []

        return data
    except Exception as e:
        logging.error(f"AI Error: {e}")
        return {"response": "Сорян, я немного подвис. Повтори? 😵", "actions": []}


def generate_reminder(cart_context):
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # СОЗДАЕМ СИНХРОННОГО КЛИЕНТА
    prompt = f"Пользователь собрал корзину: {cart_context}, но молчит 2 минуты. Напиши короткое дерзкое напоминание оформить заказ."
    try:
        # Убрали await
        res = client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "system", "content": prompt}]
        )
        return res.choices[0].message.content
    except:
        return "Эй, ты тут? Еда стынет (шутка)! Оформляем? 👀"


def get_upsell_recommendations(cart_dict, menu_items):
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # СОЗДАЕМ СИНХРОННОГО КЛИЕНТА
    # ... (логика upsell без изменений) ...
    # Копирую логику из вашего файла, чтобы не потерялась
    id_map = {str(m['id']): m for m in menu_items}
    menu_str = "\n".join([f"[{m['id']}] {m['name']} (Категория: {m.get('category', 'Разное')})" for m in menu_items])

    cart_items_desc = []
    cart_ids = []
    has_drink = False

    for k, v in cart_dict.items():
        item = id_map.get(str(k))
        if item:
            name = item['name']
            cat = item.get('category', '').lower()
            cart_items_desc.append(f"{name} [{cat}] - {v} шт")
            cart_ids.append(str(k))
            if 'напит' in cat or 'drink' in cat or 'bar' in cat or 'вода' in name.lower() or 'cola' in name.lower():
                has_drink = True
        else:
            cart_ids.append(str(k))

    cart_str = ", ".join(cart_items_desc) if cart_items_desc else "Пусто"
    forbidden_ids = ", ".join(cart_ids)
    drink_status = "ЕСТЬ НАПИТОК" if has_drink else "НЕТ НАПИТКА"

    system_prompt = (
        f"Ты — ИИ-официант. Твоя задача — ненавязчивые допродажи.\n"
        f"МЕНЮ:\n{menu_str}\n\n"
        f"КОРЗИНА: {cart_str}\n"
        f"СТАТУС: {drink_status}\n"
        f"ЗАПРЕЩЕННЫЕ ID (УЖЕ В КОРЗИНЕ): [{forbidden_ids}]\n\n"
        f"--- ПРАВИЛА ---\n"
        f"1. Не предлагай то, что уже есть.\n"
        f"2. Если {drink_status} == ЕСТЬ НАПИТОК, не предлагай воду/колу.\n"
        f"3. Верни JSON: {{ \"message\": \"...\", \"products\": [id] }}\n"
    )

    try:
        # Убрали await
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt}],
            response_format={"type": "json_object"},
            temperature=0.3
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Upsell Error: {e}")
        return {"message": "", "products": []}


def analyze_tables_for_waiter(orders_data):
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # СОЗДАЕМ СИНХРОННОГО КЛИЕНТА
    # ... (логика waiter без изменений, просто возвращаем пустой список если ошибка)
    if not orders_data: return []
    context_str = "\n".join(
        [f"Стол {o['table']} ({o['status']}), не обновлялся {o['minutes']} мин." for o in orders_data])
    system_prompt = f"Ты менеджер. Проанализируй: \n{context_str}\nВерни JSON hint."
    try:
        # Убрали await
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "system", "content": system_prompt}],
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        return data.get('hints', [])
    except:
        return []