"""Nounda YouTube cover compositor.

Ported from the approved prototype (cover-examples/cover-renderer.py). The rules it
encodes are the ones in YOUTUBE-PACKAGING-GUIDE.md §1.3:
  - warm-graded, film-grained full-bleed portrait; avatar LIT on the right
  - strong warm-dark scrim on the LEFT ONLY, fading out before it reaches the face
  - inset cream editorial frame
  - gold accent bar + uppercase kicker, top-left
  - huge cream numeral + key word on a GOLD bar (clean gap) + cream supporting line
  - white logo bottom-left with a GUARANTEED clear band above it
"""
import os
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
PF700 = os.path.join(ASSETS, "fonts", "playfair-700.ttf")
IN600 = os.path.join(ASSETS, "fonts", "inter-600.ttf")
LOGO = os.path.join(ASSETS, "logo-white.png")

GOLD, CREAM, DARKW = (0xC2, 0xAA, 0x84), (0xF7, 0xF3, 0xEB), (0x1B, 0x18, 0x14)
W, H, PAD = 1920, 1080, 127          # 1920x1080 per the guide; PAD scaled from the 1600px prototype
S = W / 1600.0                        # scale factor vs the prototype so all metrics stay proportional
MAXW = int(W * 0.44)


def _grade(im):
    """Warm matte grade + fine film grain. Matches the social-card look."""
    scale = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    # centre-crop, biased slightly high so the face sits in the upper third
    x = (im.width - W) // 2
    y = int((im.height - H) * 0.18)
    im = im.crop((max(0, x), max(0, y), max(0, x) + W, max(0, y) + H))
    im = ImageEnhance.Color(im).enhance(0.85)
    im = ImageEnhance.Brightness(im).enhance(1.03)
    im = ImageEnhance.Contrast(im).enhance(1.04)
    im = Image.blend(im, Image.new("RGB", (W, H), (236, 216, 180)), 0.10)
    im = Image.blend(im, Image.effect_noise((W, H), 24).convert("RGB"), 0.045)
    return im


def _scrim(base):
    """Horizontal-only scrim: near-black left, fully clear by ~0.58W so the face stays lit."""
    def hx(x):
        if x < 0.36 * W:
            return 0.97
        t = (x - 0.36 * W) / (0.22 * W)
        return max(0.0, 0.97 * (1 - min(1.0, t)))
    row = bytes(int(hx(x) * 255) for x in range(W))
    buf = bytearray(W * H)
    for y in range(H):
        buf[y * W:(y + 1) * W] = row
    alpha = Image.frombytes("L", (W, H), bytes(buf))
    out = base.copy()
    out.paste(Image.new("RGB", (W, H), (20, 16, 12)), (0, 0), alpha)
    return out


def _fit(path, text, target, max_w):
    size = target
    while size > 30:
        f = ImageFont.truetype(path, size)
        if max(f.getbbox(ln)[2] for ln in text.split("\n")) <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(path, size)


def _tracked(draw, xy, text, font, fill, tracking):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def render_cover(portrait: Image.Image, concept: dict) -> Image.Image:
    """portrait: PIL image of the generated avatar. concept: the cover_concept dict."""
    number = (concept.get("number") or "").strip()
    word = (concept.get("word") or "").strip()
    supporting = (concept.get("supporting") or "").strip()
    headline = (concept.get("headline") or "").strip()
    kicker = (concept.get("kicker") or "Nounda").strip()

    img = _scrim(_grade(portrait.convert("RGB"))).convert("RGBA")

    # inset editorial frame
    fr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(fr).rectangle(
        [int(44 * S), int(44 * S), W - int(44 * S), H - int(44 * S)],
        outline=(248, 244, 236, 165), width=max(2, int(3 * S)),
    )
    img = Image.alpha_composite(img, fr)
    d = ImageDraw.Draw(img)

    # kicker: gold accent bar + tracked uppercase label
    d.rectangle([PAD, PAD + int(15 * S), PAD + int(48 * S), PAD + int(18 * S)], fill=GOLD)
    _tracked(d, (PAD + int(68 * S), PAD + int(2 * S)), kicker.upper(),
             ImageFont.truetype(IN600, int(29 * S)), CREAM, 5 * S)

    top = PAD + int(70 * S)
    bot = H - PAD - int(96 * S)
    logo_px = int(80 * S)
    logo_y = H - PAD - int(44 * S)

    if number:
        nf = _fit(PF700, number, int(280 * S), MAXW)
        nb = nf.getbbox(number)
        ink_h = nb[3] - nb[1]
        wf = ImageFont.truetype(PF700, int(76 * S))
        bar_h = int(76 * S) + int(20 * S)
        sf = ImageFont.truetype(PF700, int(50 * S)) if supporting else None
        sup_lines = _wrap(supporting, sf, MAXW, d) if supporting else []
        sup_h = int(50 * S * 1.15) * len(sup_lines)
        g1, g2 = int(34 * S), int(40 * S)
        block = ink_h + g1 + bar_h + ((g2 + sup_h) if supporting else 0)
        y = top + max(0, (bot - top - block) // 2)
        # clamp: always keep a clear band above the logo
        y = max(top, min(y, (logo_y + int(22 * S)) - int(56 * S) - (block + int(46 * S))))

        d.text((PAD - nb[0], y - nb[1]), number, font=nf, fill=CREAM)
        y += ink_h + g1
        ww = d.textlength(word.upper(), font=wf)
        d.rounded_rectangle([PAD, y, PAD + ww + int(44 * S), y + bar_h], radius=3, fill=GOLD)
        d.text((PAD + int(22 * S), y + int(6 * S)), word.upper(), font=wf, fill=DARKW)
        y += bar_h
        if supporting:
            y += g2
            for ln in sup_lines:
                d.text((PAD, y), ln, font=sf, fill=CREAM)
                y += int(50 * S * 1.15)
    else:
        hf = _fit(PF700, headline, int(130 * S), MAXW)
        hl = _wrap(headline, hf, MAXW, d)
        lh = int(hf.size * 1.02)
        sf = ImageFont.truetype(PF700, int(50 * S))
        sup_lines = _wrap(supporting, sf, MAXW, d) if supporting else []
        block = lh * len(hl) + int(40 * S) + int(50 * S * 1.15) * len(sup_lines) + int(26 * S)
        y = top + max(0, (bot - top - block) // 2)
        y = max(top, min(y, (logo_y + int(22 * S)) - int(56 * S) - (block + int(46 * S))))
        for ln in hl:
            d.text((PAD, y), ln, font=hf, fill=CREAM)
            y += lh
        y += int(40 * S)
        for ln in sup_lines:
            d.text((PAD, y), ln, font=sf, fill=CREAM)
            y += int(50 * S * 1.15)
        d.rectangle([PAD, y + int(8 * S), PAD + int(100 * S), y + int(15 * S)], fill=GOLD)

    lg = Image.open(LOGO).convert("RGBA").resize((logo_px, logo_px), Image.LANCZOS)
    img.alpha_composite(lg, (PAD - int(4 * S), logo_y))
    return img.convert("RGB")


def _wrap(text, font, max_w, draw):
    """Wrap on width, honouring any explicit newlines the concept already contains."""
    if not text:
        return []
    out = []
    for para in text.split("\n"):
        words, line = para.split(), ""
        for w in words:
            trial = (line + " " + w).strip()
            if draw.textlength(trial, font=font) <= max_w or not line:
                line = trial
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
    return out
