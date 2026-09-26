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
  - a thin gold vertical rule to the left of the text block, the section-label accent
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
PF700 = os.path.join(ASSETS, "fonts", "playfair-700.ttf")

GOLD = (0xC2, 0xAA, 0x84)
CREAM = (0xF7, 0xF3, 0xEB)
MUTED = (0xD8, 0xD2, 0xC4)

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


def render_caption(hook_text: str, supporting_line: str = "") -> Image.Image:
    hook_text = (hook_text or "").strip()
    supporting_line = (supporting_line or "").strip()

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    hf, hook_lines = _fit_wrapped(d, hook_text, PF700, MAXW, start_size=92, min_size=48, max_lines=4)
    hlh = int(hf.size * 1.15)

    sf, sup_lines = (
        _fit_wrapped(d, supporting_line, IN600, MAXW, start_size=36, min_size=26, max_lines=2)
        if supporting_line else (None, [])
    )
    slh = int(sf.size * 1.3) if sf else 0

    block_gap = 26
    block_h = hlh * len(hook_lines)
    if sup_lines:
        block_h += block_gap + slh * len(sup_lines)

    safe_h = SAFE_BOTTOM - SAFE_TOP
    y0 = SAFE_TOP + max(0, (safe_h - block_h) // 2) - 40
    y0 = max(SAFE_TOP - 40, min(y0, SAFE_BOTTOM - block_h))

    # legibility: a soft dark gradient rising from the bottom, not a boxed card --
    # reads as the shot naturally darkening toward the caption, not a UI element.
    grad_h = min(H - y0 + 60, H)
    grad = Image.new("L", (1, grad_h), 0)
    for i in range(grad_h):
        grad.putpixel((0, i), int(200 * (i / grad_h) ** 1.4))
    grad = grad.resize((W, grad_h))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", (W, grad_h), (10, 8, 6, 255)), (0, H - grad_h), grad)
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    x = MARGIN + 28
    d.rectangle([MARGIN, y0, MARGIN + 6, y0 + block_h], fill=GOLD)

    y = y0
    for ln in hook_lines:
        d.text((x, y), ln, font=hf, fill=CREAM)
        y += hlh

    if sup_lines:
        y += block_gap
        for ln in sup_lines:
            d.text((x, y), ln, font=sf, fill=MUTED)
            y += slh

    return img


def render_chunk_caption(text: str) -> Image.Image:
    """A short (3-5 word) spoken-caption chunk, for Format A's word-timed overlay
    sequence -- same editorial-serif visual language as render_caption() (Playfair,
    gold rule, bottom-rising scrim) but sized for a transient phrase rather than a
    standalone headline, since a run of these plays back-to-back across real speech.
    """
    text = (text or "").strip()

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    f, lines = _fit_wrapped(d, text, PF700, MAXW, start_size=64, min_size=40, max_lines=2)
    lh = int(f.size * 1.15)
    block_h = lh * len(lines)

    safe_h = SAFE_BOTTOM - SAFE_TOP
    y0 = SAFE_TOP + max(0, (safe_h - block_h) // 2) - 40
    y0 = max(SAFE_TOP - 40, min(y0, SAFE_BOTTOM - block_h))

    # Fixed height, deliberately not derived from y0: these chunks play back-to-back and a
    # scrim that grew or shrank with the line count visibly jumped between phrases. Sized
    # to clear the highest y0 any wrap produces, so the text always sits inside it.
    grad_h = CHUNK_SCRIM_H
    grad = Image.new("L", (1, grad_h), 0)
    for i in range(grad_h):
        grad.putpixel((0, i), int(200 * (i / grad_h) ** 1.4))
    grad = grad.resize((W, grad_h))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", (W, grad_h), (10, 8, 6, 255)), (0, H - grad_h), grad)
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    x = MARGIN + 28
    d.rectangle([MARGIN, y0, MARGIN + 6, y0 + block_h], fill=GOLD)

    y = y0
    for ln in lines:
        d.text((x, y), ln, font=f, fill=CREAM)
        y += lh

    return img
