"""Nounda YouTube cover compositor.

Ported from the approved prototype (cover-examples/cover-renderer.py). The rules it
encodes are the ones in YOUTUBE-PACKAGING-GUIDE.md §1.3:
  - warm-graded, film-grained full-bleed portrait; avatar LIT on the right
  - strong warm-dark scrim on the LEFT ONLY, fading out before it reaches the face
  - inset cream editorial frame
  - white accent bar + uppercase kicker, top-left
  - number cover: figure + white unit on one baseline, then a headline that says what it means
  - headline cover: a big Playfair question/statement, up to three lines, white rule
  - white logo bottom-left with a GUARANTEED clear band above it
"""
import os
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
PF700 = os.path.join(ASSETS, "fonts", "playfair-700.ttf")
IN600 = os.path.join(ASSETS, "fonts", "inter-600.ttf")
LOGO = os.path.join(ASSETS, "logo-white.png")

# Monochrome since the site redesign (2026-10): the accent (kicker bar, unit, rule) is white, not gold.
GOLD, CREAM, DARKW = (0xFF, 0xFF, 0xFF), (0xF7, 0xF6, 0xF2), (0x1F, 0x1F, 0x1D)
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
    highlight = (concept.get("highlight") or "").strip()  # optional word or phrase of the headline to put in the chip
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
             ImageFont.truetype(IN600, int(34 * S)), CREAM, 5 * S)

    top = PAD + int(95 * S)
    bot = H - PAD - int(150 * S)
    logo_px = int(80 * S)
    logo_y = H - PAD - int(44 * S)
    centre = lambda block: top + max(0, (bot - top - block) // 2)

    if number:
        # NUMBER COVER (layout "B", Daniil 2026-09-28): figure and unit share a baseline,
        # the unit in gold, then a two-line headline that says what the number means.
        # Only for videos where the number IS the finding; otherwise use the headline cover.
        nf = ImageFont.truetype(PF700, int(200 * S))
        uf = ImageFont.truetype(PF700, int(88 * S))
        unit = word.upper()
        # A wide figure ("12.8%", "30 000") pushes the unit onto its own line below,
        # still gold and full size, rather than shrinking it into a footnote.
        stacked = d.textlength(number, font=nf) + int(24 * S) + d.textlength(unit, font=uf) > MAXW
        if stacked:
            while d.textlength(number, font=nf) > MAXW and nf.size > int(120 * S):
                nf = ImageFont.truetype(PF700, nf.size - 6)
        nb = nf.getbbox(number)
        ink_h = nb[3] - nb[1]
        ub = uf.getbbox(unit)
        unit_h = (ub[3] - ub[1]) + int(18 * S) if stacked else 0
        hf = ImageFont.truetype(PF700, int(74 * S))
        lines = _wrap(supporting, hf, MAXW, d)[:3] if supporting else []
        lh = int(hf.size * 1.1)
        block = ink_h + unit_h + (int(30 * S) + lh * len(lines) if lines else 0)
        y = centre(block)
        d.text((PAD - nb[0], y - nb[1]), number, font=nf, fill=CREAM)
        # The unit is the highlight: a white chip with a glow (see _chip).
        uf = ImageFont.truetype(PF700, int(uf.size * 0.82))
        ub = uf.getbbox(unit)
        if stacked:
            img, _ = _chip(img, PAD + int(22 * S), y + ink_h + int(40 * S), unit, uf)
            unit_h = (ub[3] - ub[1]) + int(58 * S)
        else:
            img, _ = _chip(img, PAD + d.textlength(number, font=nf) + int(46 * S),
                           y + ink_h - (ub[3] - ub[1]), unit, uf)
        d = ImageDraw.Draw(img)
        y += ink_h + unit_h + int(30 * S)
        for ln in lines:
            d.text((PAD, y), ln, font=hf, fill=CREAM)
            y += lh
    else:
        # HEADLINE COVER (layout "C"): a big Playfair question or statement, up to four
        # lines, a short gold rule under it. No numeral, no supporting line: the title
        # completes the thought.
        # Keep the highlighted phrase on one line: glue its words for wrapping, unglue to draw.
        GLUE = "\u2060"  # word joiner: not whitespace, so wrapping keeps the phrase together
        if highlight and highlight.lower() in headline.lower():
            i = headline.lower().index(highlight.lower())
            headline = headline[:i] + headline[i:i + len(highlight)].replace(" ", GLUE) + headline[i + len(highlight):]
        longest = max(headline.split(), key=len) if headline else ""
        hf = _fit(PF700, longest, int(112 * S), MAXW)  # only the longest WORD must fit the column
        lines = _wrap(headline, hf, MAXW, d)
        while len(lines) > 4 and hf.size > int(96 * S):
            hf = ImageFont.truetype(PF700, hf.size - 4)
            lines = _wrap(headline, hf, MAXW, d)
        lh = int(hf.size * 1.04)
        y = centre(lh * len(lines))
        mark = (highlight or "").strip().lower()
        lines = [ln.replace(GLUE, " ") for ln in lines]
        # Pass 1: the chip (glow and box) goes down first, so its glow never washes over text.
        # Pass 2: all plain text on top. The chip word uses a slightly smaller face to sit
        # inside the line height.
        cf = ImageFont.truetype(PF700, int(hf.size * 0.84))
        runs, yy = [], y
        for ln in lines:
            if mark and mark in ln.lower():
                yy += int(hf.size * 0.1)  # breathing room above the chip line
                k = ln.lower().index(mark)
                before, word_, after = ln[:k], ln[k:k + len(mark)], ln[k + len(mark):]
                x = PAD + int(d.textlength(before, font=hf))
                img, cw = _chip(img, x + int(16 * S), yy + int(hf.size * 0.2), word_, cf, pad_y=int(8 * S))
                d = ImageDraw.Draw(img)
                runs.append((PAD, yy, before))
                runs.append((x + cw + int(12 * S), yy, after.lstrip()))
                mark = ""
                yy += int(hf.size * 0.08)
            else:
                runs.append((PAD, yy, ln))
            yy += lh
        for rx, ry, txt in runs:
            if txt: d.text((rx, ry), txt, font=hf, fill=CREAM)
        y = yy
        d.rectangle([PAD, y + int(30 * S), PAD + int(100 * S), y + int(36 * S)], fill=GOLD)  # clear of descenders

    lg = Image.open(LOGO).convert("RGBA").resize((logo_px, logo_px), Image.LANCZOS)
    img.alpha_composite(lg, (PAD - int(4 * S), logo_y))
    return img.convert("RGB")



def _chip(img, x, y, text, font, pad_y=None):
    """The highlight: ink text on a white chip with a soft white glow around it (the monochrome
    stand-in for the old gold box, matching the site's glowing accents). x, y = top-left of the
    text box. Returns (image, chip width)."""
    from PIL import ImageFilter
    d0 = ImageDraw.Draw(img)
    tb = font.getbbox(text)
    tw, th = d0.textlength(text, font=font), tb[3] - tb[1]
    px, py = int(22 * S), (int(14 * S) if pad_y is None else pad_y)
    box = [x - px, y - py, x + tw + px, y + th + py]
    # Three layers of light so the chip reads as lit from behind: a wide soft bloom, a bright
    # halo hugging the box, and a tight edge glow.
    e = int(34 * S)
    wide = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(wide).rectangle([box[0] - e, box[1] - e, box[2] + e, box[3] + e], fill=(255, 255, 255, 120))
    img = Image.alpha_composite(img, wide.filter(ImageFilter.GaussianBlur(int(80 * S))))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rectangle(box, fill=(255, 255, 255, 255))
    halo = glow.filter(ImageFilter.GaussianBlur(int(28 * S)))
    img = Image.alpha_composite(Image.alpha_composite(img, halo), halo)
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(int(9 * S))))
    d1 = ImageDraw.Draw(img)
    d1.rectangle(box, fill=(255, 255, 255, 255))
    d1.text((x - tb[0], y - tb[1]), text, font=font, fill=DARKW)
    return img, int(tw + 2 * px)

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
