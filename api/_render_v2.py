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


def field(portrait, c):
    """Light tonal ground like the site hero; presenter feathered in on the right."""
    base = _backdrop(portrait, GREY, 0.62, 70)
    pr = _cover(portrait, 1000, H, 0.62, 0.3)
    pr = ImageEnhance.Color(pr).enhance(0.55)
    pr = ImageEnhance.Contrast(pr).enhance(1.05)
    base.paste(pr, (W - 1000, 0), _hgrad(1000, H, 0, 300, 0, 255))
    img = _grain(base, 0.08).convert("RGBA")
    d = ImageDraw.Draw(img)
    _label(d, (M, M - 6), c["label"], (31, 31, 29, 170))
    _label(d, (W - M - 150, M - 6), c["index"], (31, 31, 29, 170))
    reg, blk = ImageFont.truetype(PF400, 142), ImageFont.truetype(PF900, 160)
    y = 330
    d.text((M - 8, y), c["line1"], font=reg, fill=INK)
    d.text((M - 10, y + 152), c["line2"], font=blk, fill=INK)
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
