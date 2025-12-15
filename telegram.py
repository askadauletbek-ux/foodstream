import asyncio
import logging
import os
import requests
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- Настройки ---
load_dotenv()
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:5000")  # Важно: порт 5000, как в app.py

if not API_TOKEN:
    raise ValueError("Не указан TELEGRAM_BOT_TOKEN")

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# --- Хендлеры (Обработчики) ---

@dp.message(Command("start"))
async def send_welcome(message: types.Message, command: CommandObject = None):  # CommandObject нужен для аргументов
    """
    Обработка /start <token> для привязки к столу.
    """
    args = message.text.split()
    token = args[1] if len(args) > 1 else None

    if token:
        # Попытка привязки
        try:
            res = requests.post(f"{BACKEND_URL}/api/telegram/bind", json={
                "chat_id": str(message.chat.id),
                "token": token
            }, timeout=5)

            if res.status_code == 200:
                data = res.json()
                await message.answer(f"✅ Вы подключены к: {data['restaurant_name']}, Стол {data['table']}")
                # Сразу запускаем диалог
                await forward_message_to_brain(message, override_text="Привет! Я за столом.")
            else:
                await message.answer("❌ Неверный QR код или стол не активен.")
        except Exception as e:
            logger.error(f"Bind Error: {e}")
            await message.answer("Ошибка подключения к ресторану.")
    else:
        await message.answer("👋 Чтобы сделать заказ, отсканируйте QR-код на столе.")


@dp.message(F.text)
async def handle_text_message(message: types.Message):
    await forward_message_to_brain(message)


async def forward_message_to_brain(message: types.Message, override_text=None):
    user_text = override_text if override_text else message.text
    chat_id = str(message.chat.id)

    # Статус "печатает" (теперь ответ придет асинхронно через Celery, но юзер видит реакцию)
    await bot.send_chat_action(chat_id, action="typing")

    payload = {
        "message": user_text,
        "telegram_chat_id": chat_id,
        # restaurant_id/table_number больше не шлем, сервер берет из привязки в БД
    }

    try:
        # Отправляем в очередь (через API)
        response = requests.post(f"{BACKEND_URL}/api/chat", json=payload, timeout=5)

        if response.status_code != 200:
            await message.answer("⚠️ Ошибка сервера. Попробуйте позже.")
        else:
            # Опционально: проверить статус ответа
            data = response.json()
            if data.get("status") == "waiting_for_admin":
                await message.answer("👩‍💻 Зову оператора...")

        # Мы НЕ ждем генерации текста ответа AI здесь.
        # Ответ придет асинхронно через Celery Worker -> send_telegram_async

    except requests.exceptions.RequestException as e:
        logger.error(f"Connection Error: {e}")
        await message.answer("🔌 Не могу достучаться до кухни.")

# --- Запуск ---
async def main():
    logger.info(f"Запускаем тонкого клиента Telegram... Backend: {BACKEND_URL}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())