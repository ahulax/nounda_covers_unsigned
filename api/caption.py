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


def render_caption(hook_text: str, supporting_line: str = "", label: str = "Field notes") -> Image.Image:
    """Hook caption for Formats B and C (2026-10-28 audit): a glass card in the safe zone with a
    tracked label, the hook in two weights (set-up Playfair 400, payoff Playfair 900 in white
    with the glow) and the supporting line in Inter. Replaces the warm scrim and single weight."""
    hook_text = (hook_text or "").strip()
    supporting_line = (supporting_line or "").strip()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 54
    inner = W - 2 * MARGIN - 2 * pad
    set_up, payoff = _split_hook(hook_text)
    rf, r_lines = _fit_wrapped(d, set_up, PF400, inner, start_size=72, min_size=44, max_lines=3) if set_up else (None, [])
    bf, b_lines = _fit_wrapped(d, payoff, PF900, inner, start_size=96, min_size=56, max_lines=2) if payoff else (None, [])
    sf, s_lines = _fit_wrapped(d, supporting_line, IN400, inner, start_size=32, min_size=26, max_lines=3) if supporting_line else (None, [])
    lf = ImageFont.truetype(IN500, 24)
    rows_h = (int(rf.size * 1.12) * len(r_lines) if rf else 0) + (int(bf.size * 1.04) * len(b_lines) if bf else 0)
    card_h = pad + 30 + 34 + rows_h + ((28 + int(sf.size * 1.4) * len(s_lines)) if s_lines else 0) + pad
    card_w = W - 2 * MARGIN
    y0 = SAFE_TOP + max(0, (SAFE_BOTTOM - SAFE_TOP - card_h) // 2) - 40
    y0 = max(SAFE_TOP - 80, min(y0, SAFE_BOTTOM - card_h))
    card, shadow = _glass((card_w, card_h))
    img.alpha_composite(shadow, (MARGIN - 60, y0 - 60))
    img.alpha_composite(card, (MARGIN, y0))
    d = ImageDraw.Draw(img)
    x, y = MARGIN + pad, y0 + pad
    tx = x
    for ch in (label or "Field notes").upper():
        d.text((tx, y), ch, font=lf, fill=(247, 246, 242, 170)); tx += d.textlength(ch, font=lf) + 4
    y += 30 + 34
    for ln in r_lines:
        d.text((x, y), ln, font=rf, fill=CREAM); y += int(rf.size * 1.12)
    for ln in b_lines:
        img = _glow_text(img, (x - 2, y), ln, bf); y += int(bf.size * 1.04)
    d = ImageDraw.Draw(img)
    if s_lines:
        y += 34
        for ln in s_lines:
            d.text((x, y), ln, font=sf, fill=MUTED); y += int(sf.size * 1.4)
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


def render_chunk_caption(text: str, with_scrim: bool = True) -> Image.Image:
    """A short (3-5 word) spoken-caption chunk for Format A. Since the 2026-10-28 audit each
    chunk sits on a small glass pill (the long-form subtitle surface) so it holds on bright
    footage; the persistent scrim is still drawn under it when asked for, much softer."""
    text = (text or "").strip()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f, lines = _fit_wrapped(d, text, PF400, MAXW - 40, start_size=64, min_size=40, max_lines=2)
    lh = int(f.size * 1.18)
    tw = max(d.textlength(ln, font=f) for ln in lines) if lines else 0
    pw, ph = int(tw + 2 * 40), int(lh * len(lines) + 2 * 26)
    y0 = SAFE_TOP + max(0, (SAFE_BOTTOM - SAFE_TOP - ph) // 2)
    if with_scrim:
        img = Image.alpha_composite(img, render_chunk_scrim())
    pill, shadow = _glass((pw, ph), radius=20)
    img.alpha_composite(shadow, (MARGIN - 60, y0 - 60))
    img.alpha_composite(pill, (MARGIN, y0))
    d = ImageDraw.Draw(img)
    y = y0 + 26 - int(f.size * 0.08)
    for ln in lines:
        d.text((MARGIN + 40, y), ln, font=f, fill=CREAM); y += lh
    return img
