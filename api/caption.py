"""POST /api/caption — render a transparent 1080x1920 PNG caption overlay for short-form video.

Used by api/shortform.js (SF03 render endpoint): this endpoint does the text layout
(wrapping, fitting, brand styling) in Python/PIL, reusing the exact helpers already
proven in api/_render.py, then the Node function overlays the PNG onto video with
ffmpeg. Splitting it this way avoids native canvas bindings in the Node runtime
(node-canvas is notoriously fragile on Vercel) while keeping ffmpeg -- which Python's
serverless story handles far less reliably -- in Node.

Request body: { "hook_text": "...", "supporting_line": "..." (optional) }
Response: image/png bytes, 1080x1920, transparent background.

Layout, per SHORT-FORM-CONTENT-PRODUCT-SHEET.md's technical spec:
  - text centred in the safe vertical zone (roughly 40-60% of height)
  - clear of the bottom 350px (platform caption/audio UI) and the top ~15%
  - a soft dark rounded scrim behind the text block so it stays legible over any
    b-roll, the same legibility problem _render.py's _scrim() already solved for
    the cover portrait
  - small gold accent rule above the hook, matching the section-label convention
    used everywhere else in the brand system
"""
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

from PIL import Image, ImageDraw, ImageFont, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _render import _wrap  # noqa: E402

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
IN600 = os.path.join(ASSETS, "fonts", "inter-600.ttf")

GOLD = (0xC2, 0xAA, 0x84)
CREAM = (0xF7, 0xF3, 0xEB)
MUTED = (0xD8, 0xD2, 0xC4)

W, H = 1080, 1920
MARGIN = 84          # matches the platform right-side button-column keepout
MAXW = W - 2 * MARGIN
SAFE_TOP = int(H * 0.40)
SAFE_BOTTOM = H - 350  # platform caption/audio UI chrome


def _fit_wrapped(draw, text, max_w, start_size, min_size, max_lines):
    """Largest size where the WRAPPED text fits max_w per line and max_lines total.

    Unlike _render.py's _fit() (which sizes to fit the whole string unwrapped on one
    line -- fine for a short numeral or 2-3 word headline, wrong for a full sentence
    meant to wrap across several lines), this wraps at each candidate size and checks
    the wrapped result.
    """
    size = start_size
    while size > min_size:
        f = ImageFont.truetype(IN600, size)
        lines = _wrap(text, f, max_w, draw)
        if len(lines) <= max_lines and all(draw.textlength(ln, font=f) <= max_w for ln in lines):
            return f, lines
        size -= 4
    f = ImageFont.truetype(IN600, min_size)
    return f, _wrap(text, f, max_w, draw)


def render_caption(hook_text: str, supporting_line: str = "") -> Image.Image:
    hook_text = (hook_text or "").strip()
    supporting_line = (supporting_line or "").strip()

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    hf, hook_lines = _fit_wrapped(d, hook_text, MAXW, start_size=88, min_size=44, max_lines=4)
    hlh = int(hf.size * 1.22)

    sf, sup_lines = (
        _fit_wrapped(d, supporting_line, MAXW, start_size=44, min_size=30, max_lines=3)
        if supporting_line else (None, [])
    )
    slh = int(sf.size * 1.3) if sf else 0

    rule_h, rule_gap, block_gap = 6, 28, 32
    block_h = rule_h + rule_gap + hlh * len(hook_lines)
    if sup_lines:
        block_h += block_gap + slh * len(sup_lines)

    safe_h = SAFE_BOTTOM - SAFE_TOP
    y = SAFE_TOP + max(0, (safe_h - block_h) // 2)
    y = max(SAFE_TOP, min(y, SAFE_BOTTOM - block_h))

    # legibility scrim: soft dark rounded card behind the whole text block
    pad_x, pad_y = 56, 40
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(scrim).rounded_rectangle(
        [MARGIN - pad_x + 40, y - pad_y, W - MARGIN + pad_x - 40, y + block_h + pad_y],
        radius=28, fill=(20, 17, 13, 145),
    )
    scrim = scrim.filter(ImageFilter.GaussianBlur(1))
    img = Image.alpha_composite(img, scrim)
    d = ImageDraw.Draw(img)

    rule_w = 64
    d.rectangle([W // 2 - rule_w // 2, y, W // 2 + rule_w // 2, y + rule_h], fill=GOLD)
    y += rule_h + rule_gap

    for ln in hook_lines:
        lw = d.textlength(ln, font=hf)
        d.text(((W - lw) // 2, y), ln, font=hf, fill=CREAM)
        y += hlh

    if sup_lines:
        y += block_gap
        for ln in sup_lines:
            lw = d.textlength(ln, font=sf)
            d.text(((W - lw) // 2, y), ln, font=sf, fill=MUTED)
            y += slh

    return img


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            img = render_caption(body.get("hook_text", ""), body.get("supporting_line", ""))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            payload = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
