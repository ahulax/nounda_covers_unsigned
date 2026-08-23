"""POST /api/cover — generate the identity-locked portrait, composite the YouTube cover, upload both.

Make (S31) calls this once. Everything slow or fiddly happens here, not in Make:
  1. build the NanoBanana prompt from the cover concept (expression + setting)
  2. create a PiAPI task with the LOCKED avatar reference image
  3. poll server-side until the portrait is ready
  4. composite the 1920x1080 cover (api/_render.py)
  5. upload portrait + cover to Cloudinary
  6. return { portrait_url, thumbnail_url }

Request body (normal path — Make generates the portrait, this composites it):
  { "record_id": "rec…", "portrait_url": "https://…", "cover_concept": {...} | "json string" }

Other modes:
  "dry_run": true    composite onto the bundled reference still. Free, no generation.
  (omit both)        generate the portrait here via PiAPI, then composite.

Env vars (Vercel → Settings → Environment Variables):
  PIAPI_KEY                 PiAPI API key
  CLOUDINARY_CLOUD          e.g. dvjshgv9h
  CLOUDINARY_PRESET         an UNSIGNED upload preset that accepts images
  AVATAR_REFERENCE_URL      (optional) override the bundled reference still
"""
import base64
import io
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler

from PIL import Image

from _render import render_cover

PIAPI_KEY = os.environ.get("PIAPI_KEY", "")
CLOUD = os.environ.get("CLOUDINARY_CLOUD", "")
PRESET = os.environ.get("CLOUDINARY_PRESET", "")
SELF_BASE = os.environ.get("SELF_BASE_URL", "")  # e.g. https://nounda-cover.vercel.app
REFERENCE_URL = os.environ.get("AVATAR_REFERENCE_URL", "") or (
    f"{SELF_BASE}/assets/avatar-reference.jpg" if SELF_BASE else ""
)

PIAPI_TASK = "https://api.piapi.ai/api/v1/task"
POLL_SECONDS = 3
POLL_MAX = 15  # ~45s ceiling, inside Vercel's 60s function limit

# The guide's guardrails, applied to every generation.
GUARDRAILS = (
    "editorial photography, warm muted palette, natural window light, matte film look, "
    "upper body, subject positioned on the RIGHT side of the frame with clean empty space "
    "on the LEFT for text, shallow depth of field. "
    "No text, no words, no logos, no flags, no money, no passports, no handshakes, "
    "not glossy stock photography."
)

EXPRESSION_PROMPT = {
    "serious · concerned": "a serious, concerned expression, brow slightly drawn, direct eye contact",
    "focused · surprise": "a focused expression with a flicker of surprise, eyebrows slightly raised",
    "warm · reassuring": "a warm, reassuring expression, faint confident smile",
    "composed · direct": "a composed, authoritative expression, calm and direct",
}

SETTING_PROMPT = {
    "Haussmann office": "seated at a desk in a classic Parisian Haussmann office, tall windows, herringbone floor",
    "library": "seated in a wood-panelled library, shelves of bound books, a brass desk lamp",
    "minimalist studio": "in a minimalist studio with a warm neutral backdrop and one framed architectural drawing",
    "préfecture / administrative hall": "seated in a French administrative hall, institutional interior, muted daylight",
    "airport": "in a quiet airport terminal, large windows, soft daylight",
    "incubator / coworking": "in a modern coworking space, glass partitions, warm wood, other desks blurred behind",
}


def build_prompt(concept):
    expr = EXPRESSION_PROMPT.get(concept.get("expression", ""), EXPRESSION_PROMPT["composed · direct"])
    setting = SETTING_PROMPT.get(concept.get("setting", ""), SETTING_PROMPT["minimalist studio"])
    return (
        f"The same man as in the reference image, identical face and identity, "
        f"{setting}, {expr}, leaning very slightly forward as if explaining something. {GUARDRAILS}"
    )


def _post_json(url, payload, headers):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def generate_portrait(concept):
    """Create a NanoBanana task from the locked reference, poll until done, return the image URL."""
    if not PIAPI_KEY:
        raise RuntimeError("PIAPI_KEY is not set")
    if not REFERENCE_URL:
        raise RuntimeError("AVATAR_REFERENCE_URL / SELF_BASE_URL is not set")

    headers = {"x-api-key": PIAPI_KEY, "Content-Type": "application/json"}
    created = _post_json(PIAPI_TASK, {
        "model": "gemini",
        "task_type": "nano-banana-2",
        "input": {
            "prompt": build_prompt(concept),
            "image_urls": [REFERENCE_URL],
            "aspect_ratio": "16:9",
            "resolution": "2K",
            "output_format": "jpg",
        },
    }, headers)

    task_id = (created.get("data") or {}).get("task_id") or created.get("task_id")
    if not task_id:
        raise RuntimeError(f"no task_id in PiAPI response: {created}")

    for _ in range(POLL_MAX):
        time.sleep(POLL_SECONDS)
        req = urllib.request.Request(f"{PIAPI_TASK}/{task_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
        data = body.get("data") or body
        status = str(data.get("status", "")).lower()
        if status in ("completed", "success"):
            urls = (data.get("output") or {}).get("image_urls") or []
            if not urls:
                raise RuntimeError(f"task completed without image_urls: {data}")
            return urls[0]
        if status in ("failed", "error"):
            raise RuntimeError(f"PiAPI task failed: {data.get('error') or data}")
    raise TimeoutError("PiAPI task did not finish within the polling window")


def fetch_image(url):
    with urllib.request.urlopen(url, timeout=45) as r:
        return Image.open(io.BytesIO(r.read()))


def upload(img_bytes, public_id):
    """Unsigned Cloudinary upload. Returns the secure URL."""
    if not (CLOUD and PRESET):
        raise RuntimeError("CLOUDINARY_CLOUD / CLOUDINARY_PRESET are not set")
    boundary = "----noundacover"
    parts = []
    for key, val in (("upload_preset", PRESET), ("public_id", public_id)):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{public_id}.jpg\"\r\n"
        f"Content-Type: image/jpeg\r\n\r\n".encode()
    )
    parts.append(img_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"https://api.cloudinary.com/v1_1/{CLOUD}/image/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["secure_url"]


def handle(payload):
    """Three modes, in priority order:

    1. portrait_url supplied  -> composite only. THIS IS THE NORMAL PATH: Make generates the
       portrait with the native PiAPI module (so the prompt stays editable in Make) and passes
       the resulting URL here.
    2. dry_run: true          -> composite onto the bundled reference still. Free, instant,
       for checking layout changes.
    3. neither                -> generate here via PiAPI, then composite. Fallback for
       running the whole thing headless.
    """
    concept = payload.get("cover_concept") or {}
    if isinstance(concept, str):
        concept = json.loads(concept)
    record_id = payload.get("record_id") or "cover"
    stamp = int(time.time())

    if payload.get("portrait_url"):
        portrait_url = payload["portrait_url"]
    elif payload.get("dry_run"):
        portrait_url = REFERENCE_URL
    else:
        portrait_url = generate_portrait(concept)

    portrait = fetch_image(portrait_url)
    cover = render_cover(portrait, concept)

    buf = io.BytesIO()
    cover.save(buf, "JPEG", quality=90)   # JPEG keeps it well under YouTube's 2 MB thumbnail cap
    thumbnail_url = upload(buf.getvalue(), f"nounda-covers/{record_id}-{stamp}")

    return {"portrait_url": portrait_url, "thumbnail_url": thumbnail_url, "concept": concept}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("content-length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = handle(payload)
            code = 200
        except Exception as exc:  # surface the reason to Make instead of a bare 500
            result = {"error": f"{type(exc).__name__}: {exc}"}
            code = 500
        body = json.dumps(result).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = json.dumps({"ok": True, "reference": REFERENCE_URL}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
