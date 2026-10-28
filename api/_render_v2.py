"""Nounda YouTube covers, v2 prototypes (2026-10-28), built from the site redesign's rules
rather than recoloured from the old template:
  - grained, tonal grey ground with a blurred black-and-white photo behind it (site backdrop)
  - Playfair at two weights in one headline, regular then black ("France, / clearly.")
  - tiny tracked Inter labels, hairline rules, a "No 014" index, vertical coordinates
  - frosted glass card and the glowing white highlight from the site's accents

Three layouts:
  field  light tonal ground, presenter feathered in on the right, two-weight headline
  glass  presenter full bleed, the headline on a frosted glass card with the glow chip
  index  dark ground, one huge glowing figure, the presenter in black and white
"""
import os
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from _render import _chip, _tracked

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
F = lambda n: os.path.join(ASSETS, "fonts", n)
PF400, PF900, PF400I = F("playfair-400.ttf"), F("playfair-900.ttf"), F("playfair-400i.ttf")
IN500 = F("inter-500.ttf")
LOGO = os.path.join(ASSETS, "logo-white.png")

W, H = 1920, 1080
INK, CREAM, GREY = (0x1F, 0x1F, 0x1D), (0xF2, 0xF1, 0xEC), (0xC9, 0xC7, 0xC1)
M = 120  # outer margin


def _cover(im, w, h, fx=0.5, fy=0.35):
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x = int((im.width - w) * fx)
    y = int((im.height - h) * fy)
    return im.crop((x, y, x + w, y + h))


def _grain(img, amount=0.07):
    noise = Image.effect_noise(img.size, 60).convert("RGB")
    return Image.blend(img.convert("RGB"), noise, amount)


def _hgrad(w, h, x0, x1, a0=255, a1=0):
    """Horizontal alpha ramp from a0 at x0 to a1 at x1."""
    row = bytes(int(a0 if x < x0 else a1 if x > x1 else a0 + (a1 - a0) * (x - x0) / (x1 - x0)) for x in range(w))
    return Image.frombytes("L", (w, h), row * h)


def _backdrop(portrait, tone=GREY, mix=0.72, blur=60):
    """The site's backdrop: the photo in black and white, blurred, washed into a grey tone."""
    bg = ImageOps.grayscale(_cover(portrait, W, H)).convert("RGB")
    bg = bg.filter(ImageFilter.GaussianBlur(blur))
    return Image.blend(bg, Image.new("RGB", (W, H), tone), mix)


def _label(d, xy, text, fill, size=26, tracking=6):
    _tracked(d, xy, text.upper(), ImageFont.truetype(IN500, size), fill, tracking)


def _vertical(img, x, y, text, fill, size=22):
    f = ImageFont.truetype(IN500, size)
    tw = int(sum(ImageDraw.Draw(img).textlength(c, font=f) + 5 for c in text)) + 10
    layer = Image.new("RGBA", (tw, size + 12), (0, 0, 0, 0))
    _tracked(ImageDraw.Draw(layer), (0, 0), text, f, fill, 5)
    layer = layer.rotate(-90, expand=True)
    img.alpha_composite(layer, (x, y))


def _glow_text(img, xy, text, font, fill=(255, 255, 255), glow=(255, 255, 255), strength=1.0):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=glow + (int(255 * strength),))
    img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(46)))
    img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(14)))
    ImageDraw.Draw(img).text(xy, text, font=font, fill=fill)
    return img


LINE_W = 930  # the headline column ends before the presenter's feathered edge


def _fit_lines(text, path, start, floor, max_lines, draw):
    """Largest size from start down to floor at which text wraps into max_lines within LINE_W."""
    size = start
    while True:
        f = ImageFont.truetype(path, size)
        lines, line = [], ""
        for w in text.split():
            trial = (line + " " + w).strip()
            if draw.textlength(trial, font=f) <= LINE_W or not line:
                line = trial
            else:
                lines.append(line); line = w
        lines.append(line)
        if (len(lines) <= max_lines and all(draw.textlength(l, font=f) <= LINE_W for l in lines)) or size <= floor:
            return f, lines
        size -= 4


def field_lines(c):
    """The two-weight headline: line1 in regular, line2 (the short hook) in black, like the site's
    "France, / clearly.". Returns (line1, line2, black_first). The Visual director writes line1 and
    line2 itself; older concepts are split here, keeping the black line to the last 1 to 3 words."""
    if c.get("line1") or c.get("line2"):
        return (c.get("line1") or "").strip(), (c.get("line2") or "").strip(), False
    if (c.get("number") or "").strip():
        return (c.get("supporting") or "").strip(), f"{c['number'].strip()} {(c.get('word') or '').strip()}".strip(), True
    words = (c.get("headline") or "").split()
    k = len(words)
    while k > 1 and len(" ".join(words[k - 1:])) <= 16 and len(words) - (k - 1) <= 3:
        k -= 1
    return " ".join(words[:k]), " ".join(words[k:]), False


def field(portrait, c):
    """Approved template (Daniil, 2026-10-28): light tonal ground like the site hero, the
    presenter feathered in on the right, a two-weight Playfair headline, tracked labels."""
    base = _backdrop(portrait, GREY, 0.62, 70)
    pr = _cover(portrait, 1000, H, 0.62, 0.3)
    pr = ImageEnhance.Color(pr).enhance(0.55)
    pr = ImageEnhance.Contrast(pr).enhance(1.05)
    base.paste(pr, (W - 1000, 0), _hgrad(1000, H, 0, 300, 0, 255))
    img = _grain(base, 0.08).convert("RGBA")
    d = ImageDraw.Draw(img)
    _label(d, (M, M - 6), c.get("label") or "Field notes", (31, 31, 29, 170))
    if c.get("index"):
        f = ImageFont.truetype(IN500, 26)
        tw = sum(d.textlength(ch, font=f) + 6 for ch in c["index"])
        _label(d, (int(W - M - tw), M - 6), c["index"], (31, 31, 29, 170))
    line1, line2, black_first = field_lines(c)
    def best(text, path, start, floor, max_lines):
        # fewest lines first: one line at any size down to floor, then two, then three
        for n in range(1, max_lines + 1):
            f, ls = _fit_lines(text, path, start, floor, n, d)
            if len(ls) <= n and all(d.textlength(l, font=f) <= LINE_W for l in ls):
                return f, ls
        return f, ls
    reg, l1 = best(line1, PF400, 132, 84, 3) if line1 else (None, [])
    blk, l2 = best(line2, PF900, 168, 104, 2) if line2 else (None, [])
    rows = [(blk, ln, -10) for ln in l2] + [(reg, ln, -8) for ln in l1] if black_first \
        else [(reg, ln, -8) for ln in l1] + [(blk, ln, -10) for ln in l2]
    lh = lambda f: int(f.size * (0.98 if f is blk else 1.06))
    block = sum(lh(f) for f, _, _ in rows)
    y = max(220, (H - block) // 2 - 10)
    for f, ln, dx in rows:
        d.text((M + dx, y), ln, font=f, fill=INK); y += lh(f)
    d.line([(M, H - M), (M + 560, H - M)], fill=(31, 31, 29, 90), width=2)
    _label(d, (M, H - M + 22), "Nounda · Field notes", (31, 31, 29, 150), 22, 5)
    return img.convert("RGB")


def glass(portrait, c):
    """Presenter full bleed; the headline sits on frosted glass with the glowing chip."""
    base = _cover(portrait, W, H, 0.5, 0.3)
    base = ImageEnhance.Color(base).enhance(0.7)
    base = ImageEnhance.Brightness(base).enhance(0.92)
    # quiet the left half so the glass reads, like the site's darker rail
    base.paste(Image.new("RGB", (W, H), (24, 23, 21)), (0, 0), _hgrad(W, H, 0, 1150, 150, 0))
    img = _grain(base, 0.06).convert("RGBA")
    box = (M - 20, 150, M + 900, H - 150)
    region = img.crop(box).filter(ImageFilter.GaussianBlur(38))
    region = Image.blend(region.convert("RGB"), Image.new("RGB", region.size, (255, 255, 255)), 0.08).convert("RGBA")
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, region.width - 1, region.height - 1], 30, fill=255)
    img.paste(region, box[:2], mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, 30, outline=(255, 255, 255, 70), width=2)
    d.line([(box[0] + 40, box[1] + 2), (box[2] - 40, box[1] + 2)], fill=(255, 255, 255, 120), width=2)  # top sheen
    x = box[0] + 64
    _label(d, (x, box[1] + 60), c["label"], (242, 241, 236, 175))
    hf = ImageFont.truetype(PF400, 104)
    y = box[1] + 150
    for ln in c["glass_lines"]:
        if c.get("highlight") and c["highlight"] in ln:
            pre, post = ln.split(c["highlight"], 1)
            d.text((x, y), pre, font=hf, fill=CREAM)
            px = x + int(d.textlength(pre, font=hf))
            img, cw = _chip(img, px + 16, y + 26, c["highlight"], ImageFont.truetype(PF400, 88), pad_y=10)
            d = ImageDraw.Draw(img)
            if post:
                d.text((px + cw + 14, y), post, font=hf, fill=CREAM)
        else:
            d.text((x, y), ln, font=hf, fill=CREAM)
        y += 124
    d.line([(x, box[3] - 80), (x + 120, box[3] - 80)], fill=(255, 255, 255, 140), width=2)
    _label(d, (x + 140, box[3] - 92), "Nounda", (242, 241, 236, 150), 22, 6)
    return img.convert("RGB")


def index(portrait, c):
    """Dark ground, one huge glowing figure; presenter in black and white."""
    base = _backdrop(portrait, (30, 29, 27), 0.8, 50)
    pr = ImageOps.grayscale(_cover(portrait, 980, H, 0.62, 0.3)).convert("RGB")
    pr = ImageEnhance.Contrast(pr).enhance(1.12)
    pr = ImageEnhance.Brightness(pr).enhance(0.95)
    base.paste(pr, (W - 980, 0), _hgrad(980, H, 0, 380, 0, 255))
    img = _grain(base, 0.08).convert("RGBA")
    d = ImageDraw.Draw(img)
    _label(d, (M, M - 6), c["label"], (242, 241, 236, 170))
    d.line([(M, M + 44), (M + 820, M + 44)], fill=(242, 241, 236, 60), width=2)
    nf = ImageFont.truetype(PF900, 360)
    nb = nf.getbbox(c["number"])
    img = _glow_text(img, (M - 14 - nb[0], 250 - nb[1] + 40), c["number"], nf, strength=0.75)
    d = ImageDraw.Draw(img)
    uf = ImageFont.truetype(PF400I, 66)
    yy = 250 + (nb[3] - nb[1]) + 110
    for ln in c["unit_lines"]:
        d.text((M, yy), ln, font=uf, fill=CREAM)
        yy += 80
    _label(d, (M, H - M), c["index"] + " · Nounda", (242, 241, 236, 140), 22, 5)
    _vertical(img, W - 70, H - 330, "48.8566° N", (242, 241, 236, 140))
    return img.convert("RGB")
