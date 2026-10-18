from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "data" / "generated_reports"


def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_report(report_id: int, username: str | None, user_id: int | None, logo_path: str | None) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    width, height = 1200, 700
    image = Image.new("RGB", (width, height), (15, 17, 22))
    draw = ImageDraw.Draw(image)

    # Subtle panels and warning accent.
    draw.rounded_rectangle((35, 35, width - 35, height - 35), radius=32, outline=(85, 90, 100), width=2)
    draw.rounded_rectangle((65, 65, 1135, 635), radius=25, fill=(23, 25, 32))
    draw.ellipse((90, 100, 250, 260), fill=(170, 28, 35))
    draw.line((135, 145, 205, 215), fill="white", width=18)
    draw.line((205, 145, 135, 215), fill="white", width=18)

    if logo_path and Path(logo_path).exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((180, 90), Image.Resampling.LANCZOS)
            image.alpha_composite(logo, (900, 85)) if image.mode == "RGBA" else image.paste(logo, (900, 85), logo)
        except Exception:
            pass

    title_font = _font(52, True)
    body_font = _font(42, False)
    small_font = _font(28, False)
    name = f"@{username}" if username else "Unknown User"
    uid = str(user_id) if user_id is not None else "Unknown ID"

    draw.text((285, 125), "SCAM WARNING", font=title_font, fill=(245, 245, 245))
    draw.text((110, 330), f"❌ {name} ({uid})", font=body_font, fill=(255, 75, 75))
    draw.text((110, 405), "has been marked as a scammer.", font=body_font, fill=(240, 240, 240))
    draw.text((110, 540), "Verified through manual review workflow", font=small_font, fill=(160, 165, 175))

    path = OUTPUT_DIR / f"report_{report_id}.png"
    image.save(path, format="PNG", optimize=True)
    return str(path)
