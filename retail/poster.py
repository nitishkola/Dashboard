# retail/poster.py
# Renders a simple printable / shareable offer poster as a PNG.
import io
import textwrap

from PIL import Image, ImageDraw, ImageFont

from retail.data import money

W, H = 1080, 1350  # portrait, fits phone screens and WhatsApp status


def _font(size: int):
    for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _centered(draw, y, text, font, fill, width_chars):
    for line in textwrap.wrap(text, width=width_chars):
        box = draw.textbbox((0, 0), line, font=font)
        draw.text(((W - (box[2] - box[0])) / 2, y), line, font=font, fill=fill)
        y += (box[3] - box[1]) + 18
    return y


def render_poster(shop: str, headline: str, product: str, offer_text: str,
                  price: float, offer_price: float, bundle_with: str = "") -> bytes:
    img = Image.new("RGB", (W, H), "#FFF8E7")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 170], fill="#C0392B")
    _centered(draw, 55, shop.upper(), _font(64), "#FFFFFF", 26)

    y = _centered(draw, 260, headline, _font(96), "#C0392B", 16)
    y = _centered(draw, y + 50, product, _font(70), "#2C3E50", 22)
    if bundle_with:
        y = _centered(draw, y + 10, f"+ {bundle_with}", _font(54), "#2C3E50", 26)

    if offer_price < price:
        y = _centered(draw, y + 60, money(price, 2), _font(60), "#7F8C8D", 20)
        box = draw.textbbox((0, 0), money(price, 2), font=_font(60))
        mid = y - (box[3] - box[1]) / 2 - 18
        half = (box[2] - box[0]) / 2
        draw.line([(W / 2 - half, mid), (W / 2 + half, mid)], fill="#7F8C8D", width=6)
        y = _centered(draw, y + 10, money(offer_price, 2), _font(150), "#27AE60", 12)
    else:
        y = _centered(draw, y + 60, money(price, 2), _font(130), "#27AE60", 12)

    _centered(draw, max(y + 60, 1050), offer_text, _font(46), "#2C3E50", 36)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
