import logging
import datetime
import json
import requests # Используем requests для синхронной отправки
from models import SessionLocal, Order, OrderStatus, ChatMessage, MenuItem, ServiceSignal
import assistant
import os

# Настройка логгера
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def send_telegram_sync(chat_id, text):
    """Синхронная отправка сообщения в Telegram (через HTTP request)"""
    if not chat_id or not TELEGRAM_TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        logger.error(f"Telegram Send Error: {e}")

def process_ai_message_task(chat_id, user_text, order_id, restaurant_id, is_telegram=False):
    """
    Фоновая задача для обработки сообщения AI.
    """
    # flush=True заставляет текст появляться в консоли мгновенно
    print(f"--- [TASK] STARTING AI THREAD for Order {order_id} ---", flush=True)

    # УБРАЛИ ASYNCIO LOOP

    try:
        with SessionLocal() as db:
            order = db.query(Order).get(order_id)
            if not order:
                print("--- [TASK] ERROR: Order not found", flush=True)
                return

            cart_dict = {str(i.menu_item_id): i.quantity for i in order.items}

            history_objs = db.query(ChatMessage).filter(ChatMessage.order_id == order.id) \
                .order_by(ChatMessage.timestamp.desc()).limit(6).all()
            history = [{"role": "assistant" if m.sender == 'bot' else "user", "content": m.content}
                       for m in reversed(history_objs)]

            menu_items = [{"id": i.id, "name": i.name, "price": i.price} for i in
                          db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id).all()]

            print(f"--- [TASK] Calling OpenAI... (User: {user_text})", flush=True)

            ai_response = {"response": "Извините, я задумалась...", "actions": []}
            try:
                # ВАЖНО: Тут может быть ошибка ключа API
                # ВЫЗЫВАЕМ СИНХРОННО (без loop.run_until_complete)
                ai_response = assistant.process_message(
                    user_text=user_text,
                    cart=cart_dict,
                    menu_items=menu_items,
                    chat_history=history
                )
            except Exception as e:
                print(f"--- [TASK] AI ERROR (Check API Key!): {e}", flush=True)
                ai_response = {"response": "Ошибка соединения с ИИ. Проверьте консоль сервера.", "actions": []}

            bot_text = ai_response.get('response', '...')
            actions = ai_response.get('actions', [])
            recommendations = ai_response.get('recommendations', [])

            print(f"--- [TASK] AI Answer: {bot_text[:50]}... Actions: {len(actions)}", flush=True)

            if actions:
                try:
                    from services import execute_actions
                    execute_actions(db, order, actions, restaurant_id)
                    print("--- [TASK] Actions executed successfully", flush=True)
                except Exception as e:
                    print(f"--- [TASK] ACTION ERROR: {e}", flush=True)
                    db.rollback()
                    bot_text += "\n(Не удалось обновить корзину)"

            try:
                if recommendations:
                    content_data = {"text": bot_text, "items": recommendations}
                    db.add(ChatMessage(order_id=order.id, sender='bot', content=json.dumps(content_data),
                                       message_type='suggestion'))
                else:
                    db.add(ChatMessage(order_id=order.id, sender='bot', content=bot_text))

                db.commit()
                print("--- [TASK] Message saved to DB", flush=True)
            except Exception as e:
                print(f"--- [TASK] DB SAVE ERROR: {e}", flush=True)
                db.rollback()

            if is_telegram and chat_id:
                # ВЫЗЫВАЕМ СИНХРОННУЮ ОТПРАВКУ
                send_telegram_sync(chat_id, bot_text)

    except Exception as e:
        print(f"--- [TASK] CRITICAL THREAD FAILURE: {e}", flush=True)
    finally:
        # УБРАЛИ LOOP.CLOSE()
        print("--- [TASK] Thread finished", flush=True)

def check_reminders_task():
    """Периодическая задача."""
    # УБРАЛИ ASYNCIO LOOP
    try:
        with SessionLocal() as db:
            timeout = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
            stale = db.query(Order).filter(
                Order.status == OrderStatus.BASKET_ASSEMBLY,
                Order.is_bot_active == True,
                Order.reminder_sent == False,
                Order.last_activity < timeout
            ).all()

            for o in stale:
                if not o.items: continue
                txt = "Не забудьте оформить заказ! 🍕"
                db.add(ChatMessage(order_id=o.id, sender='bot', content=txt))
                o.reminder_sent = True
                db.commit()
    except Exception:
        pass
    # УБРАЛИ FINALLY LOOP CLOSE