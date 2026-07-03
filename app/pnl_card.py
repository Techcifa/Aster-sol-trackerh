from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os

CARD_WIDTH = 1080
CARD_HEIGHT = 1080

COLOR_BG = (18, 18, 24)
COLOR_GREEN = (34, 197, 94)
COLOR_RED = (239, 68, 68)
COLOR_WHITE = (245, 245, 245)
COLOR_GRAY = (140, 140, 150)

# Safe Font Loading
def _get_fonts():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bold_path = os.path.join(base_dir, "assets", "fonts", "DejaVuSans-Bold.ttf")
    regular_path = os.path.join(base_dir, "assets", "fonts", "DejaVuSans.ttf")
    
    try:
        font_bold_64 = ImageFont.truetype(bold_path, size=64)
        font_bold_120 = ImageFont.truetype(bold_path, size=120)
        font_regular_48 = ImageFont.truetype(regular_path, size=48)
        font_regular = ImageFont.truetype(regular_path, size=28)
        return font_bold_64, font_bold_120, font_regular_48, font_regular
    except Exception as exc:
        print(f"[pnl_card] WARNING: Failed to load TrueType fonts: {exc}. Falling back to default font.")
        # Return default font instances (Pillow uses the same font for everything when fallback is active)
        default_font = ImageFont.load_default()
        return default_font, default_font, default_font, default_font

def generate_pnl_card(data: dict) -> BytesIO:
    """
    data keys:
      - token_symbol: str
      - wallet_label: str          (label or shortened address)
      - is_closed: bool
      - pnl_sol: float
      - pnl_pct: float
      - avg_cost_sol: float
      - current_or_exit_price_sol: float
      - holding_duration_str: str      (e.g. "3h 12m" or "2d 4h")
      - held_amount: float | None      (None if fully closed)
    """
    font_bold_64, font_bold_120, font_regular_48, font_regular = _get_fonts()
    
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(img)

    is_profit = data["pnl_sol"] >= 0
    accent = COLOR_GREEN if is_profit else COLOR_RED
    sign = "+" if is_profit else ""

    # Header
    draw.text((60, 60), f"${data['token_symbol'].upper()}", font=font_bold_64, fill=COLOR_WHITE)
    status_label = "CLOSED" if data["is_closed"] else "OPEN"
    draw.text((60, 150), f"{status_label} POSITION", font=font_regular, fill=COLOR_GRAY)

    # Top Right Branding ("Aster")
    aster_text = "Aster"
    try:
        bbox = font_bold_64.getbbox(aster_text)
        text_width = bbox[2] - bbox[0]
    except Exception:
        try:
            text_width, _ = draw.textsize(aster_text, font=font_bold_64)
        except Exception:
            text_width = 150
    draw.text((CARD_WIDTH - 60 - text_width, 60), aster_text, font=font_bold_64, fill=COLOR_WHITE)

    # Big PnL number — the focal point
    pnl_text = f"{sign}{data['pnl_pct']:.1f}%"
    draw.text((60, 270), pnl_text, font=font_bold_120, fill=accent)
    draw.text((60, 420), f"{sign}{data['pnl_sol']:.4f} SOL", font=font_regular_48, fill=accent)

    # Details block
    y = 570
    line_gap = 60
    details = [
        ("Wallet", data["wallet_label"]),
        ("Avg Entry", f"{data['avg_cost_sol']:.8f} SOL"),
        ("Exit / Current", f"{data['current_or_exit_price_sol']:.8f} SOL"),
        ("Held For", data["holding_duration_str"]),
    ]
    if data.get("held_amount") is not None and data.get("held_amount") > 0:
        details.append(("Remaining", f"{data['held_amount']:,.0f} {data['token_symbol']}"))

    for label, value in details:
        draw.text((60, y), label, font=font_regular, fill=COLOR_GRAY)
        draw.text((320, y), str(value), font=font_regular, fill=COLOR_WHITE)
        y += line_gap

    # Footer branding
    draw.text((60, CARD_HEIGHT - 90), "Aster Intelligence", font=font_regular, fill=COLOR_GRAY)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
