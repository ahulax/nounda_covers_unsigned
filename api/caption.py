"""render_caption() — build a transparent 1080x1920 PNG caption overlay for short-form video.

Imported by api/index.py and dispatched on POST /api/caption (see the note in index.py
above the "/api/video-url" section: Vercel bundles this whole project into ONE Python
Lambda, so a second file here is never actually invoked as its own function — index.py
has to import and call this directly, dispatching on self.path, exactly like it already
does for _broll.select_broll()). This file holds ONLY the rendering logic, no HTTP
handling, for that reason.

Used by api/shortform.js (SF03 render endpoint): this does the text layout (wrapping,
fitting, brand styling) in Python/PIL, reusing the exact helpers already proven in
api/_render.py, then the Node function overlays the PNG onto video with ffmpeg.
Splitting it this way avoids native canvas bindings in the Node runtime (node-canvas is
notoriously fragile on Vercel) while keeping ffmpeg -- which Python's serverless story
handles far less reliably -- in Node.

Design: "editorial serif" (chosen by Daniil 2026-09-11 over three alternatives —
a dark rounded card, a bold all-caps stroked style, and a lower-third bar; see
short-form-pipeline/design-samples/ for the comparison renders):
  - Playfair Display headline (matches the website's own typography, unlike the
    Inter-only first pass) instead of a sans-serif hook
  - left-aligned, not centred, echoing the brand's narrow-left-lane editorial layout
    rather than an Instagram-meme centred caption
  - a soft dark gradient rising from the bottom instead of a boxed/rounded card --
    legible over any b-roll without reading as a UI element pasted on top of the shot
  - a thin white vertical rule to the left of the text block, the section-label accent
    used everywhere else in the brand system, doing here what the horizontal rule +
    centring did in the first pass
Layout constraints unchanged from the first pass, per SHORT-FORM-CONTENT-PRODUCT-SHEET.md's
technical spec: text sits in the safe vertical zone (roughly 40-60% of height), clear of
the bottom 350px (platform caption/audio UI) and the top ~15%.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _render import _wrap  # noqa: E402

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
IN600 = os.path.join(ASSETS, "fonts", "inter-600.ttf")
IN500 = os.path.join(ASSETS, "fonts", "inter-500.ttf")
IN400 = os.path.join(ASSETS, "fonts", "inter-400.ttf")
PF700 = os.path.join(ASSETS, "fonts", "playfair-700.ttf")
PF400 = os.path.join(ASSETS, "fonts", "playfair-400.ttf")
PF900 = os.path.join(ASSETS, "fonts", "playfair-900.ttf")

# Brand tokens (api/brand_tokens.json, synced from nounda-agents/shared/brand): monochrome.
GOLD = (0xFF, 0xFF, 0xFF)  # the accent is white since 2026-10 (name kept for callers)
CREAM = (0xF7, 0xF6, 0xF2)
MUTED = (0xB3, 0xB3, 0xAD)
INK = (0x1F, 0x1F, 0x1D)

W, H = 1080, 1920
MARGIN = 84          # matches the platform right-side button-column keepout
MAXW = W - 2 * MARGIN - 40  # minus the left rule + its gap to the text
SAFE_TOP = int(H * 0.40)
SAFE_BOTTOM = H - 350  # platform caption/audio UI chrome
CHUNK_SCRIM_H = 940    # constant across Format A chunks so the scrim never jumps or flashes


def _fit_wrapped(draw, text, font_path, max_w, start_size, min_size, max_lines):
    """Largest size where the WRAPPED text fits max_w per line and max_lines total.

    Unlike _render.py's _fit() (which sizes to fit the whole string unwrapped on one
    line -- fine for a short numeral or 2-3 word headline, wrong for a full sentence
    meant to wrap across several lines), this wraps at each candidate size and checks
    the wrapped result.
    """
    size = start_size
    while size > min_size:
        f = ImageFont.truetype(font_path, size)
        lines = _wrap(text, f, max_w, draw)
        if len(lines) <= max_lines and all(draw.textlength(ln, font=f) <= max_w for ln in lines):
            return f, lines
        size -= 4
    f = ImageFont.truetype(font_path, min_size)
    return f, _wrap(text, f, max_w, draw)


def _split_hook(text):
    """Two-weight hook like the site's "France, / clearly.": the set-up in regular, the last
    one to three words (at most 16 characters) in black."""
    words = text.split()
    k = len(words)
    while k > 1 and len(" ".join(words[k - 1:])) <= 16 and len(words) - (k - 1) <= 3:
        k -= 1
    return " ".join(words[:k]), " ".join(words[k:])


def _glass(size, radius=24):
    """The glass card on video (brand surface "glass"): warm dark tint, grain, a hairline
    border and a top sheen. The overlay cannot blur the footage behind it, so the frost is
    built from the tint and grain, like the video plates."""
    from PIL import ImageFilter
    w, h = size
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    tint = Image.new("RGBA", size, (31, 31, 29, 0))
    td = ImageDraw.Draw(tint)
    for y in range(h):  # 160deg-ish: lighter at the top
        a = int(222 - 14 * (1 - y / h))
        td.line([(0, y), (w, y)], fill=(40 + int(18 * (1 - y / h)), 39 + int(18 * (1 - y / h)), 36 + int(18 * (1 - y / h)), a))
    noise = Image.effect_noise(size, 70).convert("L").point(lambda v: int(v * 0.10))
    grain = Image.new("RGBA", size, (255, 255, 255, 0)); grain.putalpha(noise)
    tint = Image.alpha_composite(tint, grain)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
    card.paste(tint, (0, 0), mask)
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius, outline=(255, 255, 255, 56), width=2)
    d.line([(radius, 2), (w - radius, 2)], fill=(255, 255, 255, 96), width=2)
    shadow = Image.new("RGBA", (w + 120, h + 120), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([60, 80, w + 60, h + 70], radius, fill=(0, 0, 0, 90))
    return card, shadow.filter(ImageFilter.GaussianBlur(30))


def _glow_text(img, xy, text, font, strength=0.8):
    from PIL import ImageFilter
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=(255, 255, 255, int(255 * strength)))
    img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(26)))
    img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(8)))
    ImageDraw.Draw(img).text(xy, text, font=font, fill=(255, 255, 255, 255))
    return img


def _glass_deco(img, box, radius, light=False):
    """What the overlay draws for a frosted panel (the blur itself is done by ffmpeg through the
    matching mask): a light dark tint for legibility, a faint white lift, grain, a bright hairline
    border and a top sheen, like the approved thumbnail option B card."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
    tint = Image.new("RGBA", (w, h), (31, 31, 29, 58 if light else 92))
    tint = Image.alpha_composite(tint, Image.new("RGBA", (w, h), (255, 255, 255, 10 if light else 14)))
    noise = Image.effect_noise((w, h), 70).convert("L").point(lambda v: int(v * 0.08))
    g = Image.new("RGBA", (w, h), (255, 255, 255, 0)); g.putalpha(noise)
    tint = Image.alpha_composite(tint, g)
    layer.paste(tint, (0, 0), m)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius, outline=(255, 255, 255, 150), width=2)
    d.line([(radius, 3), (w - radius, 3)], fill=(255, 255, 255, 170), width=2)
    img.alpha_composite(layer, (x0, y0))
    return img


def _mask(boxes, radius):
    """White where the footage should be blurred (the glass shapes), black elsewhere."""
    m = Image.new("L", (W, H), 0); d = ImageDraw.Draw(m)
    for b in boxes:
        d.rounded_rectangle([int(v) for v in b], radius, fill=255)
    return m.convert("RGB")


def render_caption(hook_text: str, supporting_line: str = "", label: str = "Field notes", mask: bool = False) -> Image.Image:
    """Hook caption for Formats B and C: a compact frosted glass card (ffmpeg blurs the footage
    through the mask=True version of this layout) with a tracked label, the hook in two weights
    (set-up Playfair 400, payoff Playfair 900 in white with the glow) and the supporting line.
    Daniil, 2026-10-30: the full-width card covered a quarter of the frame and hid the footage, so
    the card now hugs its text, the type is smaller and the glass is lighter, low in the safe zone."""
    hook_text = (hook_text or "").strip()
    supporting_line = (supporting_line or "").strip()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad_x, pad_y = 36, 30
    inner = W - 2 * MARGIN - 2 * pad_x - 60
    set_up, payoff = _split_hook(hook_text)
    rf, r_lines = _fit_wrapped(d, set_up, PF400, inner, start_size=52, min_size=36, max_lines=3) if set_up else (None, [])
    bf, b_lines = _fit_wrapped(d, payoff, PF900, inner, start_size=62, min_size=42, max_lines=2) if payoff else (None, [])
    sf, s_lines = _fit_wrapped(d, supporting_line, IN400, inner, start_size=26, min_size=22, max_lines=2) if supporting_line else (None, [])
    lf = ImageFont.truetype(IN500, 20)
    lab = (label or "Field notes").upper()
    lab_w = sum(d.textlength(ch, font=lf) + 4 for ch in lab)
    widths = [lab_w] + [d.textlength(ln, font=rf) for ln in r_lines] + [d.textlength(ln, font=bf) for ln in b_lines] + [d.textlength(ln, font=sf) for ln in s_lines]
    card_w = int(max(widths) + 2 * pad_x)
    rows_h = (int(rf.size * 1.12) * len(r_lines) if rf else 0) + (int(bf.size * 1.06) * len(b_lines) if bf else 0)
    card_h = pad_y + 22 + 22 + rows_h + ((18 + int(sf.size * 1.4) * len(s_lines)) if s_lines else 0) + pad_y - 6
    box = (MARGIN, SAFE_BOTTOM - card_h - 20, MARGIN + card_w, SAFE_BOTTOM - 20)
    if mask:
        return _mask([box], 22)
    img = _glass_deco(img, box, 22, light=True)
    d = ImageDraw.Draw(img)
    x, y = box[0] + pad_x, box[1] + pad_y
    tx = x
    for ch in lab:
        d.text((tx, y), ch, font=lf, fill=(247, 246, 242, 210)); tx += d.textlength(ch, font=lf) + 4
    y += 22 + 22
    for ln in r_lines:
        d.text((x, y), ln, font=rf, fill=CREAM); y += int(rf.size * 1.12)
    for ln in b_lines:
        img = _glow_text(img, (x - 2, y), ln, bf); y += int(bf.size * 1.06)
    d = ImageDraw.Draw(img)
    if s_lines:
        y += 18
        for ln in s_lines:
            d.text((x, y), ln, font=sf, fill=(235, 234, 229)); y += int(sf.size * 1.4)
    return img


def render_chunk_scrim() -> Image.Image:
    """The bottom-rising scrim on its own, with no text.

    Format A plays a run of chunk captions back to back. Baking the scrim into every
    chunk meant it could only be on screen while that chunk's words were, so it blinked
    out in the pauses between phrases. Holding a chunk longer to cover the gap desynced
    the words from the mouth instead. The scrim is therefore its own layer, shown for the
    whole clip, and the text chunks sit on top of it only while they are actually spoken.
    """
    grad = Image.new("L", (1, CHUNK_SCRIM_H), 0)
    for i in range(CHUNK_SCRIM_H):
        grad.putpixel((0, i), int(120 * (i / CHUNK_SCRIM_H) ** 1.6))
    grad = grad.resize((W, CHUNK_SCRIM_H))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (W, CHUNK_SCRIM_H), (31, 31, 29, 255)), (0, H - CHUNK_SCRIM_H), grad)
    return img


def render_chunk_caption(text: str, with_scrim: bool = True, mask: bool = False) -> Image.Image:
    """A short (3-5 word) spoken-caption chunk for Format A: each line on its own slim frosted
    glass pill that hugs the words (real blur through the mask=True version), low in the safe
    zone so the presenter's face stays clear (Daniil, 2026-10-28)."""
    text = (text or "").strip()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f, lines = _fit_wrapped(d, text, PF400, MAXW - 40, start_size=60, min_size=40, max_lines=2)
    lh = int(f.size * 1.34)
    px, py = 26, 13
    boxes, y = [], SAFE_BOTTOM - lh * len(lines) - 40
    for ln in lines:
        bb = f.getbbox(ln)
        boxes.append((MARGIN - px, y + bb[1] - py, MARGIN + d.textlength(ln, font=f) + px, y + bb[3] + py))
        y += lh
    if mask:
        return _mask(boxes, 18)
    if with_scrim:
        img = Image.alpha_composite(img, render_chunk_scrim())
    for b in boxes:
        img = _glass_deco(img, b, 18)
    d = ImageDraw.Draw(img)
    y = SAFE_BOTTOM - lh * len(lines) - 40
    for ln in lines:
        d.text((MARGIN, y), ln, font=f, fill=CREAM); y += lh
    return img
