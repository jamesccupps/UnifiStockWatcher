"""Compose site/images/social-preview.png (1280x640) from the app icon and a
screenshot: the picture link previews show, and the repository's Settings →
Social preview (uploaded by hand).

    python scripts/make_social_preview.py     (needs Pillow; run demo.py --shots first)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "site" / "images"
W, H = 1280, 640


def font(size, bold=False):
    for name in (("seguisb.ttf" if bold else "segoeui.ttf"), "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img = Image.new("RGB", (W, H))
    top, bottom = (18, 49, 80), (14, 20, 27)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))

    shot = Image.open(IMAGES / "watch-list-dark.png").convert("RGB")
    shot.thumbnail((640, 580))
    x, y = W - shot.width - 40, (H - shot.height) // 2 + 30
    shadow = Image.new("RGBA", (shot.width + 60, shot.height + 60), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([30, 30, shot.width + 30, shot.height + 30], 12, fill=(0, 0, 0, 150))
    img.paste(shadow.filter(ImageFilter.GaussianBlur(14)), (x - 30, y - 18), shadow.filter(ImageFilter.GaussianBlur(14)))
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width - 1, shot.height - 1], 10, fill=255)
    img.paste(shot, (x, y), mask)

    icon = Image.open(ROOT / "assets" / "icon.png").convert("RGBA").resize((104, 104), Image.LANCZOS)
    img.paste(icon, (64, 92), icon)
    d = ImageDraw.Draw(img)
    d.text((64, 222), "UniFi Stock", font=font(64, True), fill="white")
    d.text((64, 296), "Watcher", font=font(64, True), fill="white")
    lines = ["Know the moment it's back", "in stock on the Ubiquiti Store."]
    for i, line in enumerate(lines):
        d.text((66, 398 + i * 40), line, font=font(30), fill=(169, 195, 221))
    d.text((66, 520), "Free and open source · Windows 10 and 11", font=font(22), fill=(143, 168, 194))
    out = IMAGES / "social-preview.png"
    img.save(out, optimize=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
