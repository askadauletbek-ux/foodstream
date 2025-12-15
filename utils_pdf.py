import qrcode
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def register_fonts():
    """Регистрируем шрифт с поддержкой кириллицы (Arial)."""
    try:
        pdfmetrics.registerFont(TTFont('Arial', 'arial.ttf'))
        return 'Arial'
    except:
        try:
            pdfmetrics.registerFont(TTFont('Arial', 'DejaVuSans.ttf'))
            return 'Arial'
        except:
            return 'Helvetica'


def draw_pill_shape(c, x, y, width, height, color):
    """Рисует овальную кнопку (пилюлю)."""
    radius = height / 2
    c.setFillColor(color)
    c.setStrokeColor(color)
    c.roundRect(x, y, width, height, radius, fill=1, stroke=0)


def generate_qr_pdf(restaurant_name, restaurant_slug, tables_list, domain="http://127.0.0.1:5000"):
    """
    tables_list: список словарей/объектов [{'number': 1, 'public_token': 'abc...'}, ...]
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    font_name = register_fonts()

    cols = 2
    rows = 2
    card_width = width / cols
    card_height = height / rows

    # --- ПАЛИТРА ---
    COLOR_BG = HexColor("#FFFBEB")  # Теплый кремовый фон
    COLOR_DARK = HexColor("#1E1B4B")  # Глубокий Индиго (Текст)
    COLOR_ACCENT = HexColor("#F97316")  # Яркий Коралл (Акценты)
    COLOR_BRAND = HexColor("#9CA3AF")  # Серый (Футер)

    total_tables = len(tables_list)
    processed_count = 0

    while processed_count < total_tables:
        for r in range(rows - 1, -1, -1):
            for col in range(cols):
                if processed_count >= total_tables: break

                table_obj = tables_list[processed_count]
                table_num = table_obj.number
                table_token = table_obj.public_token

                # Базовые координаты ячейки
                x_base = col * card_width
                y_base = r * card_height
                cx = x_base + card_width / 2
                cy = y_base + card_height / 2

                # 1. ФОН ЯЧЕЙКИ
                c.setFillColor(COLOR_BG)
                c.rect(x_base, y_base, card_width, card_height, fill=1, stroke=0)

                # Линии отреза (пунктир)
                c.setStrokeColor(HexColor("#FCD34D"))
                c.setLineWidth(0.5)
                c.setDash(4, 4)
                c.rect(x_base, y_base, card_width, card_height)
                c.setDash([], 0)

                # --- РИСУЕМ БЛОК QR (Снизу вверх, чтобы не перекрывать) ---

                # Координаты для QR
                # Смещаем QR чуть ниже центра, чтобы сверху влезло название
                qr_block_size = 65 * mm
                qr_y_center = cy - 5 * mm

                qr_bg_x = cx - qr_block_size / 2
                qr_bg_y = qr_y_center - qr_block_size / 2

                # 2. МЯГКАЯ ТЕНЬ (Полупрозрачная)
                c.saveState()
                # Черный цвет с прозрачностью 10% (0.1)
                c.setFillColor(Color(0, 0, 0, alpha=0.1))
                # Тень рисуем чуть ниже и правее основного блока
                c.roundRect(qr_bg_x + 2 * mm, qr_bg_y - 2 * mm, qr_block_size, qr_block_size, 6 * mm, fill=1, stroke=0)
                c.restoreState()

                # 3. БЕЛАЯ ПОДЛОЖКА ПОД QR
                c.setFillColor(HexColor("#FFFFFF"))
                c.setStrokeColor(HexColor("#FFFFFF"))
                c.roundRect(qr_bg_x, qr_bg_y, qr_block_size, qr_block_size, 6 * mm, fill=1, stroke=0)

                # 4. САМ QR КОД (Теперь используем токен!)
                link = f"{domain}/r/{restaurant_slug}?t={table_token}"
                qr = qrcode.QRCode(box_size=10, border=0)
                qr.add_data(link)
                qr.make(fit=True)

                # Темный QR на белом фоне
                img = qr.make_image(fill_color="#1E1B4B", back_color="white")

                img_buffer = io.BytesIO()
                img.save(img_buffer, format='PNG')
                img_buffer.seek(0)
                qr_image = ImageReader(img_buffer)

                qr_img_size = 50 * mm
                # Центрируем изображение внутри белого блока
                c.drawImage(qr_image, cx - qr_img_size / 2, qr_y_center - qr_img_size / 2, width=qr_img_size,
                            height=qr_img_size)

                # --- ТЕКСТОВЫЕ ЭЛЕМЕНТЫ (Рисуем ПОВЕРХ всего) ---

                # 5. НОМЕР СТОЛА (Сверху, "Пилюля")
                pill_w = 40 * mm
                pill_h = 10 * mm
                pill_y = y_base + card_height - 20 * mm

                draw_pill_shape(c, cx - pill_w / 2, pill_y, pill_w, pill_h, COLOR_ACCENT)

                c.setFillColor(HexColor("#FFFFFF"))
                c.setFont(font_name, 12)
                c.drawCentredString(cx, pill_y + 3 * mm, f"ҮСТЕЛ / СТОЛ {table_num}")

                # 6. НАЗВАНИЕ РЕСТОРАНА
                c.setFillColor(COLOR_DARK)
                c.setFont(font_name, 16)
                c.drawCentredString(cx, pill_y - 12 * mm, restaurant_name.upper())

                # 7. ПРИЗЫВ К ДЕЙСТВИЮ (Под QR)
                c.setFillColor(COLOR_DARK)
                c.setFont(font_name, 14)
                c.drawCentredString(cx, qr_bg_y - 8 * mm, "МӘЗІРДІ АШУ")

                c.setFillColor(HexColor("#6B7280"))
                c.setFont(font_name, 9)
                c.drawCentredString(cx, qr_bg_y - 13 * mm, "Наведите камеру для заказа")

                # 8. ФУТЕР (В самом низу)
                c.setFillColor(COLOR_BRAND)
                c.setFont(font_name, 7)
                c.drawCentredString(cx, y_base + 8 * mm, "Powered by FoodStream")

                processed_count += 1

        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer