#!/usr/bin/env python3
import argparse
import string
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

CANDIDATES = string.digits + string.ascii_letters + string.punctuation + " "

MONO_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
]

def glyph_coverage(ch, font):
    """Anteil 'Tinte' eines Zeichens an seiner Zelle (0..1)."""
    img = Image.new("L", (64, 64), 0)
    d = ImageDraw.Draw(img)
    try:
        d.text((32, 32), ch, fill=255, font=font, anchor="mm")
    except Exception:  # Bitmap-Font ohne Anchor-Support
        d.text((16, 16), ch, fill=255, font=font)
    return sum(img.getdata()) / (255 * 64 * 64)

def build_ramp(levels):
    """Zeichen nach real gemessener Deckung sortieren,
    dann `levels` gleichmäßig verteilte Stufen auswählen."""
    font = None
    for path in MONO_FONTS:
        try:
            font = ImageFont.truetype(path, 40)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    cov = {ch: glyph_coverage(ch, font) for ch in CANDIDATES}
    ordered = sorted(cov, key=cov.get)
    levels = max(2, min(levels, len(ordered)))

    lo, hi = cov[ordered[0]], cov[ordered[-1]]
    used, ramp = set(), []
    for i in range(levels):
        target = lo + (hi - lo) * i / (levels - 1)
        ch = min((c for c in ordered if c not in used),
                 key=lambda c: abs(cov[c] - target))
        used.add(ch)
        ramp.append(ch)
    return ramp  # aufsteigend: leer -> dicht

def image_to_ascii(path, width, levels, ratio, gamma, blur, invert):
    ramp = build_ramp(levels)
    if invert:  # für helle Terminal-Hintergründe
        ramp.reverse()

    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = img.size
    new_h = max(1, round(width * (h / w) * ratio))

    try:
        box = Image.Resampling.BOX
    except AttributeError:
        box = Image.BOX
    img = img.resize((width, new_h), box)   # Flächenmittelung pro Zelle
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    pixels = img.load()
    n = len(ramp)
    lines = []
    for y in range(new_h):
        row = []
        for x in range(width):
            r, g, b = pixels[x, y]
            lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
            lum = min(1.0, lum ** (1.0 / gamma))
            ch = ramp[round(lum * (n - 1))]
            row.append(" " if ch == " " else f"\x1b[38;2;{r};{g};{b}m{ch}")
        lines.append("".join(row) + "\x1b[0m")
    print("\n".join(lines))

def main():
    p = argparse.ArgumentParser(description="Truecolor-ASCII-Konverter")
    p.add_argument("image")
    p.add_argument("width", nargs="?", type=int, default=100)
    p.add_argument("--levels", type=int, default=16,
                   help="Dichtestufen (weniger = glatter, z.B. 10-16)")
    p.add_argument("--ratio", type=float, default=0.5,
                   help="Zellen-Höhe/Breite deines Fonts (meist 0.5)")
    p.add_argument("--gamma", type=float, default=1.0,
                   help="<1 dunkler, >1 hellere Mitteltöne")
    p.add_argument("--blur", type=float, default=0.0,
                   help="Gaussian-Radius gegen Bildrauschen (z.B. 0.5-1)")
    p.add_argument("--invert", action="store_true",
                   help="für helle Terminal-Hintergründe")
    a = p.parse_args()
    image_to_ascii(a.image, a.width, a.levels, a.ratio, a.gamma, a.blur, a.invert)

if __name__ == "__main__":
    main()
