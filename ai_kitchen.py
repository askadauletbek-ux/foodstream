import os
import io
import secrets
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from google import genai
from google.genai import types
from PIL import Image

ai_bp = Blueprint('ai_kitchen', __name__)

# --- КОНФИГУРАЦИЯ ---
# Модель Nano Banana (Gemini 2.5 Flash Image) для редактирования фото
MODEL_NAME = "gemini-2.5-flash-image-preview"

# Строгий промпт: Реализм + Фон + Угол
FOOD_STYLE_PROMPT = (
    "Enhance this food photo to look like a high-end Michelin star restaurant photograph. "
    "CRITICAL: Keep the exact food ingredients, portion size, and plating arrangement identical to the original image. "
    "Do NOT add new elements. Do NOT turn it into a cartoon or illustration. "

    # --- ФОН И АТМОСФЕРА ---
    "Background: Place the dish on a polished dark stone or premium wood table. "
    "The background must be a softly blurred, elegant fine-dining restaurant interior with warm bokeh lights. "
    "Ensure the lighting feels natural and cinematic, highlighting the texture of the food. "

    # --- УГОЛ И РАКУРС ---
    "Angle: Use a professional 45-degree angle (appetizing angle) to showcase the volume and layers of the dish. "
    "Focus sharply on the food, with a shallow depth of field (blurry background). "

    "Style: Hyper-realistic, 8k resolution, macro photography details. "
    "Fix any blur, noise, or bad lighting from the original. Make textures look juicy and appetizing."
)


@ai_bp.route("/api/menu/generate-image-google", methods=["POST"])
@login_required
def generate_food_image_google():
    if current_user.role not in ['admin', 'super_admin']:
        return jsonify({"error": "Доступ запрещен"}), 403

    if 'file' not in request.files:
        return jsonify({"error": "Нет файла"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Файл не выбран"}), 400

    try:
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

        # 1. КОНВЕРТАЦИЯ (Исправление ошибки MIME type и формата)
        image_bytes = file.read()
        try:
            img = Image.open(io.BytesIO(image_bytes))
            # Убираем прозрачность и приводим к RGB
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')

            # Сохраняем в буфер как качественный JPEG
            output_buffer = io.BytesIO()
            img.save(output_buffer, format='JPEG', quality=95)
            processed_image_bytes = output_buffer.getvalue()
            mime_type = 'image/jpeg'
        except Exception as e:
            print(f"Image conversion error: {e}")
            # Фоллбек на исходные данные
            processed_image_bytes = image_bytes
            mime_type = file.mimetype

        # 2. ОТПРАВКА В GEMINI (Фото + Промпт)
        contents = [
            types.Part.from_bytes(data=processed_image_bytes, mime_type=mime_type),
            types.Part.from_text(text=FOOD_STYLE_PROMPT)
        ]

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],  # Требуем вернуть изображение
            )
        )

        # 3. ПОЛУЧЕНИЕ РЕЗУЛЬТАТА
        generated_image_part = None
        for part in response.parts:
            if part.inline_data:
                generated_image_part = part
                break

        if not generated_image_part:
            text_resp = response.text if response.text else "Изображение не сгенерировано (цензура или ошибка)"
            print(f"Gemini Refusal: {text_resp}")
            return jsonify({"error": f"AI не смог обработать фото: {text_resp}"}), 500

        # 4. СОХРАНЕНИЕ
        gen_image_bytes = generated_image_part.inline_data.data

        filename = f"ai_gen_{secrets.token_hex(8)}.png"
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)

        with open(save_path, "wb") as f:
            f.write(gen_image_bytes)

        public_url = f"/static/uploads/{filename}"

        return jsonify({
            "image_url": public_url,
            "description": "AI Enhanced Photo"
        })

    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500