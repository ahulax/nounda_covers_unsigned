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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

import boto3
from PIL import Image

# Vercel's Python runtime does not put the entrypoint's own directory on sys.path,
# so a plain `from _render import …` raises ModuleNotFoundError at cold start.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _render import render_cover  # noqa: E402
from _broll import select_broll  # noqa: E402
from caption import render_caption  # noqa: E402

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

# The guide's guardrails, applied to every generation. Two hard-won lessons baked in:
# (1) naming a setting (e.g. "library") makes the model reach for an associative prop
#     (a book) unless explicitly forbidden -> the no-props line is load-bearing.
# (2) the reference still shows one outfit, so without an explicit instruction to vary
#     it the identity-lock silently locks the wardrobe too.
GUARDRAILS = (
    "He is wearing different smart-casual clothing than the reference photo, such as a "
    "fine knit sweater, a plain shirt with no jacket and sleeves rolled up, or a different "
    "dark blazer, never the same exact jacket-and-shirt combination as the reference every "
    "time. His hands are empty and relaxed, or gesturing naturally as if explaining "
    "something. He is NOT holding a book, papers, a pen, a folder, or any object of any "
    "kind. Editorial photography, warm muted palette, natural window light, matte film "
    "look, photorealistic with natural skin texture, upper body, subject positioned on the "
    "RIGHT side of the frame with clean empty space on the LEFT for text, shallow depth of "
    "field. No text, no words, no logos, no flags, no money, no passports, no handshakes, "
    "not glossy stock photography, not an illustration."
)

EXPRESSION_PROMPT = {
    "serious · concerned": "a serious, concerned expression, subtle and natural, not exaggerated",
    "focused · surprise": "a focused expression with a subtle flicker of surprise, natural not exaggerated",
    "warm · reassuring": "a warm, reassuring expression, a faint natural smile",
    "composed · direct": "a composed, authoritative expression, calm and direct, natural not exaggerated",
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


USER_AGENT = "nounda-cover/1.0 (+https://github.com/ahulax/nounda_covers_unsigned)"


def _urlopen(req, timeout):
    """urlopen wrapper that always sets a real User-Agent (CDNs/APIs commonly 403
    the default Python-urllib UA) and surfaces the response body on HTTPError."""
    req.add_header("User-Agent", USER_AGENT)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {e.reason} for {req.full_url}: {body}") from e


def _post_json(url, payload, headers):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with _urlopen(req, timeout=30) as r:
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
        with _urlopen(req, timeout=30) as r:
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
    req = urllib.request.Request(url)
    with _urlopen(req, timeout=45) as r:
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
    with _urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["secure_url"]


# --- /api/video-url: mint a fresh presigned S3 URL on demand -------------------
# Vercel bundles this whole project into a SINGLE Python Lambda (confirmed via
# the deployment's lambdaRuntimeStats, which reports exactly one "python"
# runtime no matter how many separate entries vercel.json's `functions` key
# lists) and routes every /api/* request into it. So a second file — even a
# more specific nested one like api/video-url/index.py — is never actually
# invoked; do_GET below has to dispatch on self.path itself instead of relying
# on Vercel to pick a different file.
#
# Fixes the "S3 URL expired" bug: presigned S3 URLs are hard-capped at 7 days
# by AWS, but a video can sit in "Ready to publish" for longer than that
# before S14 runs. This mints a FRESH URL at the moment of use by reading the
# record's stored URL only for its bucket+key (the signature/expiry portion
# is discarded, so a long-stale stored URL is still a valid pointer to the
# right file) and re-signing it with a new 7-day window.
#
#   GET /api/video-url?record_id=recLKh7RP1HO5yLrO             -> 302 redirect
#   GET /api/video-url?record_id=recLKh7RP1HO5yLrO&format=json -> {"video_url": "..."}

AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "")
AWS_REGION = os.environ.get("REMOTION_AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.environ.get("REMOTION_AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("REMOTION_AWS_SECRET_ACCESS_KEY", "")

VIDEO_BASE_ID = "appraw1aDLqrHLY7q"
VIDEO_TABLE_ID = "tbliFOuhLJF1x4CmV"  # Video Production
VIDEO_FIELD_NAME = "Final Video URL"
VIDEO_URL_EXPIRES_IN = 604800  # 7 days, AWS's hard ceiling for SigV4 presigned URLs


def _fetch_stored_video_url(record_id: str) -> str:
    if not AIRTABLE_PAT:
        raise RuntimeError("AIRTABLE_PAT is not set")
    url = f"https://api.airtable.com/v0/{VIDEO_BASE_ID}/{VIDEO_TABLE_ID}/{record_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AIRTABLE_PAT}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Airtable fetch failed: {e.code} {e.read().decode()[:300]}") from e
    stored = (data.get("fields") or {}).get(VIDEO_FIELD_NAME)
    if not stored:
        raise RuntimeError(f"record {record_id} has no '{VIDEO_FIELD_NAME}' value")
    return stored


def _bucket_and_key(stored_url: str):
    """Extract bucket + key from either URL shape S3 has produced for us:
    https://<bucket>.s3.<region>.amazonaws.com/<key>?...  (boto3 virtual-hosted)
    https://<bucket>.s3.amazonaws.com/<key>?...           (legacy virtual-hosted)
    Signature/expiry query params are discarded — only bucket+key survive.
    """
    parsed = urllib.parse.urlparse(stored_url)
    host = parsed.netloc
    bucket = host.split(".s3.")[0].split(".s3")[0]
    key = urllib.parse.unquote(parsed.path.lstrip("/"))
    if not bucket or not key:
        raise RuntimeError(f"could not parse bucket/key from stored URL host={host!r} path={parsed.path!r}")
    return bucket, key


def fresh_video_url_for(record_id: str) -> str:
    stored = _fetch_stored_video_url(record_id)
    bucket, key = _bucket_and_key(stored)
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=VIDEO_URL_EXPIRES_IN
    )


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
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/caption":
            self._caption()
            return
        try:
            length = int(self.headers.get("content-length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/broll":
                result = select_broll(
                    payload.get("queries") or [],
                    per_segment=int(payload.get("per_segment") or 8),
                    scene_seconds=payload.get("scene_seconds") or {},
                )
            else:
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
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/video-url":
            self._video_url()
            return
        body = json.dumps({"ok": True, "reference": REFERENCE_URL}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _video_url(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        record_id = (qs.get("record_id") or [""])[0]
        fmt = (qs.get("format") or ["redirect"])[0]

        if not record_id:
            self._json(400, {"error": "missing ?record_id="})
            return
        try:
            url = fresh_video_url_for(record_id)
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        if fmt == "json":
            self._json(200, {"video_url": url})
        else:
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _caption(self):
        try:
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            img = render_caption(body.get("hook_text", ""), body.get("supporting_line", ""))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
